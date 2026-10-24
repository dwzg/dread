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
question; `dread llm providers` lists them; `dread llm batches` lists a provider's last Message Batches
and cancels those named (--cancel ID...: a run killed outright, SIGKILL, cannot cancel its own).

Requests go out with urllib (no SDK). Keys are read from the environment only; nothing here writes a
key anywhere. A rate limit or an overload (HTTP 429, 529, 500, 502-504) is asked again up to retries
times (default 4), after the server's retry-after or retry_wait seconds (default 5), three times
longer each time.

Every answer of a provider (but a played one, Scripted) adds a line to a usage ledger (USAGE,
USAGE_COLUMNS: time, provider, model, input and output tokens, how it ended, the tokens read from and
written to a prompt cache, the mode, "batch" for an answer of a Message Batch, and the cache's tokens
written for an hour; no text of the conversation, no key; docs/formats.md), so that what a month's runs
spent can be told:
usage_summary(), `dread pilot --usage`. A provider's price_in and price_out (US dollars per million
tokens, from its price list) turn the tokens into money, each line priced as it came (answer_cost);
without them only the tokens are summed.

A message's content is a text, or a list of parts ({"text": ..., "cache": True}) for a question whose
first part many questions share (a function's source that several runs' names are judged against, a
function's code that a vote's samples show, a group's question asked samples times): Anthropic's
provider marks a part with cache to be kept (cache_control, its prompt cache: a later question with
the same beginning reads it for a tenth of the price, 0.05 on Sonnet 5.5 and Opus 5.5, the first
writes it for a quarter more, for an hour, cache "1h", at twice the price; Anthropic's list in 2026-10;
price_cache_read, price_cache_write and price_cache_write_1h override, in US dollars per million
tokens), the others join the parts into one text (content_text), with a blank line between parts,
which is what Anthropic's model reads too (the API puts a message's blocks together as they are,
measured with its token count 2026-10-24: so its blocks after the first begin with the blank line).
A prompt shorter than the model's minimum is not kept (512 tokens for Haiku 5.5, Sonnet 5.5 and Opus
5.5; 4,096 for Haiku 4.5): a provider whose configuration gives cache_min marks a part only where
the prompt up to it reaches that (estimated at CHARS_PER_TOKEN, chars_per_token), and only such a
provider says that it caches (Provider.cacheable), which is what the pilot asks before it marks a
question or orders questions for the cache (dread.pilot shared_parts). Without cache_min a caller's own
marks go out as they are and the provider claims no cache. A cache entry is there for a question only
once the answer that writes it has begun: a question that should read it is asked after that one.

    [providers.haiku]
    kind = "anthropic"
    model = "claude-haiku-5-5"
    price_in = 0.10
    price_out = 0.50
    cache_min = 512                   # the model's least cacheable prompt (Anthropic's docs, 2026-10)

Message Batches (Anthropic's: half the standard price for input and output, the cache's prices
halved too, most answered within the hour, at most 24): a provider that can batch (can_batch;
batch = false in its configuration turns it off) takes requests (params(): the body ask() would send,
under a custom_id) in batch_submit, says how a batch stands in batch_state, gives its results in
batch_results and cancels it in batch_cancel. ask_batch() asks a list of questions so: as few batches
as BATCH_REQUESTS and BATCH_BYTES allow, looked at every BATCH_POLL seconds until each has ended (or
BATCH_WAIT seconds have passed: then it is cancelled, the answers it has are taken and the questions
it had not answered fail with an error that says so), the answers matched back by custom_id and
recorded as a batch's (mode "batch", price_batch of the standard prices, BATCH by default); an
errored result of the server's kind (RETRY_ERRORS) is asked again alone, as a question asked alone
would have been; an interrupted wait cancels its batches. A conversation (a generator: it yields each
question as (system, messages, temperature) and is sent (answer, last, error) back, its return value
the result) is one logic for both ways of asking: converse() asks its questions one after another,
converse_all() the questions of many in rounds, a round of enough questions as Message Batches and
the questions the answers lead to (a correction asked for, a question asked again) in the next round.
A Providers hands out a provider per thread for the questions asked one at a time and keeps the one
that asks the batches.

Apart from a provider's prompt cache, AnswerCache keeps whole answers by their question in a file: a
caller that asks the same question again (a judge's verdicts on a run judged before) takes the kept
answer and asks nothing. Only callers that pass one use it (dread pilot's grounded judge).

A run's meter (Meter, installed for the run with metered(); dread pilot does so for each of its runs,
since 2026-10-24) sees every question a provider asks and every answer it records in that process: the
kinds implement _ask, and Provider.ask admits each question through the meter first (Meter.admit),
ask_batch a batch's questions (Meter.admit_many), and record() counts each answer's cost (the price the
usage ledger gives it, also put into last as "cost", with the answer's number in the run as "answer").
With a budget (dread pilot --budget) a question whose estimated cost does not fit what is left after the
spend and the estimates of the questions being answered is not asked: BudgetReached (a ProviderError,
its words beginning "not asked"), the first such question marking the budget reached for the caller to
ask nothing new. The meter also hands the run's events to its emit (dread.pilotlog: each answer, each
round of converse_all, each batch submitted, looked at and ended, the budget reached;
docs/formats.md).
"""
import collections
import json
import os
import threading
import urllib.error
import urllib.request

CONFIG = os.path.join(os.path.expanduser("~"), ".config", "dread", "llm.toml")
USAGE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state"),
                     "dread", "llm-usage.tsv")
USAGE_COLUMNS = ("time", "provider", "model", "input", "output", "finish", "cache_read", "cache_write", "mode",
                 "cache_write_1h")

CHARS_PER_TOKEN = 3.0       # characters a token takes in the estimate against a model's cache minimum (provider:
                            # chars_per_token): FreeRTOS's naming questions, C made from machine code, took 2.4,
                            # English prose about 4.5 (2026-10-24); a mark on a shorter prefix is ignored by the API
                            # at no cost, a mark left out costs the saving
BATCH = 0.5                 # a Message Batch's answers against the standard prices (Anthropic's list, 2026-10;
                            # provider: price_batch)
BATCH_POLL = 10.0           # seconds between two looks at a batch that has not ended (provider: batch_poll)
BATCH_WAIT = 7200.0         # seconds a batch may take before it is cancelled and its questions fail (provider:
                            # batch_wait; the API lets a batch run 24 hours, most end within one)
BATCH_REQUESTS = 10000      # questions one batch holds at most (provider: batch_requests; the API's limit 100,000)
BATCH_BYTES = 100 * 2 ** 20  # and bytes of their requests (provider: batch_bytes; the API's limit 256 MB)
CANCEL_WAIT = 120.0         # seconds a cancelled batch is waited for, to take the answers it has (provider: cancel_wait)
OUT_TOKENS = 4000           # tokens out the budget's estimate takes for an answer before the run (or an earlier run:
                            # Meter's prior) has had one of its kind: about the longest the naming questions took on
                            # FreeRTOS GCC -Os with Haiku 5.5 and its reasoning (a globals' answer 3,818 on average, a
                            # structures' 3,948; a readable one 584, a name 390, a group's 396; 2026-10-24): an answer
                            # longer than its estimate can take the spend past the budget, a shorter one only makes a
                            # run weigh again (with 1,000 the globals' questions of a first run cost 3 times their
                            # estimates)
OUT_WINDOW = 50             # the answers of a kind whose mean tokens out the estimate takes
BATCH_LOOK = 60.0           # seconds after which a look at a batch that changed nothing is an event again
STOP_WAIT = 20.0            # seconds a stopped pool of questions waits for those being asked (thread_pool; dread gui
                            # sends SIGTERM and kills the job 30 s later: the run's end event comes before)


class ProviderError(Exception):
    pass


class BudgetReached(ProviderError):
    """A question that was not asked: its estimated cost would take the run's spend past its budget
    (Meter.admit, Meter.admit_many). Its words begin with "not asked"."""


METER = None                # the meter of the run in this process (metered), or None: no budget, no events
_asking = threading.local()     # .ticket: the question this thread is asking now (its answer's record() finds it)


class Provider:
    """ask(system, messages, temperature=None) -> text; messages are [{"role": "user"|"assistant",
    "content": str or [parts]}] (a list of parts: see the module); temperature, when given, the
    sampling temperature of this question in place of the configuration's (an OpenAI-compatible
    server takes it; Anthropic's API is sent it, and Haiku 5.5 and Sonnet 5.5 answer any other value
    than their default 1.0 with HTTP 400, "`temperature` is deprecated for this model", tried
    2026-10-22; a command provider cannot pass it on). After each
    answer, last says how it ended: {"finish": the provider's reason ("stop", "length", "end_turn",
    "max_tokens", ...), "tokens": tokens written or None, "reasoning": characters of reasoning the
    provider returned apart from the answer, "input" and the prompt cache's tokens where the provider
    tells them, "cost": the answer's price in US dollars or None without prices (record()), "answer": its
    number in the run where a run's meter counts it (Meter)}."""

    kind = None
    can_batch = False                       # whether batch_submit and its kin work (Message Batches)
    ledgered = True                         # whether record() writes the usage ledger (a played provider does not)
    CUT = ("length", "max_tokens")          # finish reasons that mean: out of room
    REFUSED = ("refusal", "content_filter")  # finish reasons that mean: the provider declined (asking the
                                             # same again gets the same: Anthropic's classifiers answer so)

    def __init__(self, name, config):
        self.name, self.config = name, dict(config)
        self.model = self.config.get("model", "?")
        self.last = {}
        self.quick = False                  # (ask without reasoning: the configuration's quick fields, if any)
        self.used = {"answers": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "batch": 0,
                     "dollars": 0.0}        # (spent(): batch, the answers of Message Batches; dollars, their cost)

    def label(self):
        return "%s (%s)" % (self.name, self.model)

    def record(self, mode=None):
        """Add the last answer's tokens to the usage ledger (a failure to write is not an error) and to
        this object's own count (spent()); mode "batch" for an answer of a Message Batch (priced at the
        batch rate, price_batch). The answer's cost goes into last ("cost", None without prices) and, with
        a run's meter installed (METER), to the meter, which numbers the answer in the run (last's
        "answer") and makes it an event."""
        import time
        info = self.last
        dollars = answer_cost(self.config, self.kind, info.get("input") or 0, info.get("tokens") or 0,
                              info.get("cache_read") or 0, info.get("cache_write") or 0,
                              info.get("cache_write_1h") or 0, mode)
        info["cost"] = dollars
        self.used["answers"] += 1
        self.used["input"] += info.get("input") or 0
        self.used["output"] += info.get("tokens") or 0
        self.used["cache_read"] += info.get("cache_read") or 0
        self.used["cache_write"] += info.get("cache_write") or 0
        self.used["batch"] += mode == "batch"
        self.used["dollars"] += dollars or 0.0
        meter = METER
        if meter is not None:
            meter.answered(self, info, mode, dollars)
        if not self.ledgered:
            return
        try:
            os.makedirs(os.path.dirname(USAGE), exist_ok=True)
            new = not os.path.exists(USAGE)
            with open(USAGE, "a") as f:
                if new:
                    f.write("\t".join(USAGE_COLUMNS) + "\n")
                f.write("%s\t%s\t%s\t%d\t%d\t%s\t%d\t%d\t%s\t%d\n" % (
                    time.strftime("%Y-%m-%dT%H:%M:%S"), self.name, self.model, info.get("input") or 0,
                    info.get("tokens") or 0, info.get("finish") or "", info.get("cache_read") or 0,
                    info.get("cache_write") or 0, mode or "", info.get("cache_write_1h") or 0))
        except OSError:
            pass

    def spent(self):
        """What this provider object used, its own answers only: answers (batch: of them through Message
        Batches), tokens in (cache_read and cache_write: of them read from and written to a prompt
        cache) and out, and the cost where the configuration gives prices (price_in, price_out; each
        answer priced as it came: a batch's at the batch rate, a cache's tokens at theirs). The ledger is
        shared: a run that counted the difference of its totals counted every run beside it too (six
        pilot runs at once, 2026-10-21, each reported about six times its own tokens)."""
        out = {k: v for k, v in self.used.items() if k != "dollars"}
        out["cost"] = self.used["dollars"] if prices_of(self.config, self.kind) else None
        return out

    def ask(self, system, messages, temperature=None):
        """The answer's text (see the class). With a run's meter installed (METER) the question is
        admitted first (Meter.admit: BudgetReached, and nothing asked, when its estimated cost does not
        fit what is left of the run's budget) and its answer counted as the run's. The kinds implement
        _ask."""
        meter = METER
        if meter is None:
            return self._ask(system, messages, temperature)
        with meter.admit(self, system, messages):
            return self._ask(system, messages, temperature)

    def _ask(self, system, messages, temperature=None):
        raise NotImplementedError

    def params(self, system, messages, temperature=None):
        """A question as one request of a batch (batch_submit): the body ask() would send."""
        raise NotImplementedError

    def cacheable(self, system, content):
        """Whether the parts of a question marked for a prompt cache would be kept (Anthropic: its
        cache_min); a provider without a prompt cache keeps none."""
        return False

    def quick_fields(self):
        """The request's fields of a question asked without reasoning (quick set by the caller): the
        configuration's quick table (Haiku 5.5: {thinking = {type = "disabled"}}; a reasoning model
        behind llama-server: {chat_template_kwargs = {enable_thinking = false}}), {} without one or when
        not quick."""
        return dict(self.config.get("quick") or {}) if self.quick else {}

    RETRY = (429, 500, 502, 503, 504, 529)  # rate limits and overloads: asked again after a wait

    def _post(self, url, body, headers):
        """The reply to a request, as JSON (_call)."""
        return self._call(url, body, headers)

    def _call(self, url, body, headers, method="POST", raw=False):
        """The reply to a request (body: JSON, or None for none), as JSON, or as text with raw. A rate
        limit or an overload (RETRY) is asked again up to retries times (default 4), after the server's
        retry-after or retry_wait seconds (default 5), three times longer each time: a run of thousands of
        questions meets them."""
        import time
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        tries = int(self.config.get("retries", 4)) + 1
        wait = float(self.config.get("retry_wait", 5))
        for attempt in range(tries):
            request = urllib.request.Request(url, data=data, method=method,
                                             headers=dict({"content-type": "application/json"}, **headers))
            try:
                with urllib.request.urlopen(request, timeout=float(self.config.get("timeout", 1800))) as response:
                    text = response.read().decode()
                    return text if raw else json.loads(text)
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

    def _ask(self, system, messages, temperature=None):
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
        body.update(self.quick_fields())
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

    @property
    def can_batch(self):
        return bool(self.config.get("batch", True))

    def blocks(self, content, system=""):
        """A message's content as Anthropic takes it: a text as it is; parts as text blocks, each after
        the first beginning with the blank line content_text joins them with (the API puts a message's
        blocks together as they are: the model reads the text content_text gives, the answer cache's key),
        a part with cache marked to be kept (cache_control: the prompt up to its end is kept for later
        questions; cache "1h" for an hour, else Anthropic's five minutes), unless the provider's cache_min
        (the model's least cacheable prompt in tokens, from its price list) says the prompt up to there is
        shorter (estimated, chars_per_token): such a mark would be ignored anyway."""
        if isinstance(content, str):
            return content
        least = self.config.get("cache_min")
        per = float(self.config.get("chars_per_token", CHARS_PER_TOKEN))
        out, length = [], len(system or "")
        for k, part in enumerate(content):
            text = ("\n\n" if k else "") + part.get("text", "")
            length += len(text)
            block = {"type": "text", "text": text}
            if part.get("cache") and (least is None or length / per >= float(least)):
                block["cache_control"] = dict({"type": "ephemeral"}, **({"ttl": "1h"} if part["cache"] == "1h"
                                                                        else {}))
            out.append(block)
        return out

    def cacheable(self, system, content):
        """Whether a question's parts marked for the cache would be kept: the configuration gives the
        model's cache_min and the prompt up to a marked part reaches it (blocks). Without cache_min the
        provider claims no cache: callers then mark nothing new (a caller's own marks, the grounded
        judge's, still go out as they are)."""
        return self.config.get("cache_min") is not None and isinstance(content, list) and any(
            "cache_control" in b for b in self.blocks(content, system))

    def params(self, system, messages, temperature=None):
        body = {"model": self.model, "max_tokens": int(self.config.get("max_tokens", 16000)), "system": system,
                "messages": [dict(m, content=self.blocks(m["content"], system if k == 0 else ""))
                             for k, m in enumerate(messages)]}
        if temperature is not None:         # (only when asked for: see Provider)
            body["temperature"] = float(temperature)
        body.update(self.quick_fields())
        return body

    def headers(self):
        key = os.environ.get(self.config.get("key_env", "ANTHROPIC_API_KEY"), "")
        if not key:
            raise ProviderError("%s: no key in $%s" % (self.name, self.config.get("key_env", "ANTHROPIC_API_KEY")))
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}

    def url(self, path):
        return self.config.get("url", "https://api.anthropic.com").rstrip("/") + path

    def take(self, reply, mode=None):
        """The text of a message (an answer of /v1/messages, or of a batch: mode "batch"), how it ended
        and its tokens in last, recorded (Provider.record)."""
        try:
            usage = reply.get("usage") or {}
            self.last = {"finish": reply.get("stop_reason"), "tokens": usage.get("output_tokens"),
                         "input": sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                                  "cache_read_input_tokens")),
                         "cache_read": usage.get("cache_read_input_tokens") or 0,
                         "cache_write": usage.get("cache_creation_input_tokens") or 0,
                         "cache_write_1h": (usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0,
                         "reasoning": sum(len(b.get("thinking", "")) for b in reply["content"]
                                          if b.get("type") == "thinking")}
            self.record(mode)
            return "".join(block.get("text", "") for block in reply["content"] if block.get("type") == "text")
        except (KeyError, TypeError, AttributeError):
            raise ProviderError("%s: unexpected reply %s" % (self.name, json.dumps(reply)[:300]))

    def _ask(self, system, messages, temperature=None):
        headers = self.headers()
        return self.take(self._post(self.url("/v1/messages"), self.params(system, messages, temperature), headers))

    def batch_submit(self, requests):
        """A new Message Batch of requests ([(custom_id, params)]): the API's batch object (id,
        processing_status, ...)."""
        return self._call(self.url("/v1/messages/batches"),
                          {"requests": [{"custom_id": c, "params": p} for c, p in requests]}, self.headers())

    def batch_state(self, batch_id):
        return self._call(self.url("/v1/messages/batches/%s" % batch_id), None, self.headers(), method="GET")

    def batch_cancel(self, batch_id):
        return self._call(self.url("/v1/messages/batches/%s/cancel" % batch_id), None, self.headers())

    def batch_list(self, limit=20):
        """The workspace's last batches, newest first (the API's batch objects)."""
        return self._call(self.url("/v1/messages/batches?limit=%d" % int(limit)), None, self.headers(),
                          method="GET").get("data", [])

    def batch_results(self, batch):
        """{custom_id: result} of an ended batch (its results_url: JSON lines; a result is {"type":
        "succeeded", "message": ...}, or "errored" with its error, "canceled", "expired")."""
        url = batch.get("results_url") or self.url("/v1/messages/batches/%s/results" % batch["id"])
        out = {}
        for line in self._call(url, None, self.headers(), method="GET", raw=True).splitlines():
            if line.strip():
                row = json.loads(line)
                out[row.get("custom_id")] = row.get("result") or {}
        return out


def content_text(content):
    """A message's content as one text: a text as it is, a list of parts ({"text": ..., "cache": ...})
    joined by blank lines."""
    if isinstance(content, str):
        return content
    return "\n\n".join(part.get("text", "") for part in content or [])


class AnswerCache:
    """Answers kept by their question, in a file of JSON lines ({"key", "answer"}), so that a question
    asked again is answered from the file and costs nothing: a judge's verdicts on a naming run that
    another comparison judged before (dread pilot --grounded, since 2026-10-23). The key is a digest of
    the provider's kind and model, the system part, the messages' texts (parts joined: content_text,
    whether a part is marked to be cached or not) and the attempt (the n-th asking of the same question:
    a question asked again because its answer missed something gets its own answer, replayed in the
    same order). An answer is taken as it was, empty or unusable too; a provider's error is not kept.
    A line the file cannot parse (a run stopped while writing) is skipped. Replaying a kept answer is
    deterministic; a measurement of the provider's own noise asks without the cache. Threads may share
    one (a lock)."""

    def __init__(self, path):
        import threading
        self.path, self.lock, self.answers, self.hits = path, threading.Lock(), {}, 0
        if path and os.path.exists(path):
            with open(path) as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and isinstance(row.get("key"), str) and isinstance(row.get("answer"), str):
                        self.answers[row["key"]] = row["answer"]

    @staticmethod
    def key(provider, system, messages, attempt=0):
        import hashlib
        text = json.dumps([provider.kind, provider.model, system,
                           [[m.get("role"), content_text(m.get("content"))] for m in messages], attempt])
        return hashlib.sha256(text.encode()).hexdigest()

    def get(self, key):
        """The kept answer to a question's key, or None (counted in hits when found)."""
        with self.lock:
            answer = self.answers.get(key)
            if answer is not None:
                self.hits += 1
            return answer

    def put(self, key, answer):
        """Keep an answer: in memory and as a line of the file (made with its directory when needed)."""
        with self.lock:
            self.answers[key] = answer
            if self.path:
                os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
                with open(self.path, "a") as f:
                    f.write(json.dumps({"key": key, "answer": answer}) + "\n")


CACHE_READ, CACHE_WRITE = 0.1, 1.25     # Anthropic's prompt cache against its input price (its list, 2026-10:
CACHE_WRITE_1H = 2.0                    # reads 0.1 on Haiku 5.5, 0.05 on Sonnet 5.5 and Opus 5.5, whose
                                        # configuration says so in price_cache_read; writes for an hour 2)


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


def answer_cost(config, kind, input_tokens, output_tokens, cache_read=0, cache_write=0, cache_write_1h=0, mode=None):
    """US dollars of one answer (or of a sum of answers alike) at a provider's prices (prices_of), or
    None without prices: the tokens written to the cache for an hour (of cache_write) at
    price_cache_write_1h (else the input price times CACHE_WRITE_1H for an anthropic provider), and the
    whole at price_batch of the standard prices (BATCH for an anthropic provider) for mode "batch"."""
    prices = prices_of(config, kind)
    if prices is None:
        return None
    anthropic = (kind or config.get("kind")) == "anthropic"
    hour = min(cache_write_1h, cache_write)
    dollars = cost(prices, input_tokens, output_tokens, cache_read, cache_write - hour) + hour * (
        float(config.get("price_cache_write_1h", prices[0] * (CACHE_WRITE_1H if anthropic else 1))) - prices[0]) / 1e6
    if mode == "batch":
        dollars *= float(config.get("price_batch", BATCH if anthropic else 1))
    return dollars


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

    def _ask(self, system, messages, temperature=None):
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
    answer may be a dict {"text": ..., "finish": ..., "tokens": ..., "input": ...} to play how it ended
    and its tokens. With batches (polls: the looks a batch takes before it ends; never: it does not
    end), it also plays Message Batches in memory: batch_submit answers each request as ask() would and
    keeps the batch in submitted (its requests' custom_ids and params), its answers recorded as a
    batch's (10 tokens in and 5 out unless the answer says); an answer that is a dict with "error" plays
    an errored result. prices: configuration fields ({"price_in": ..., "price_out": ...}, price_batch):
    its answers then cost what a provider with those prices would charge, and those asked one at a time
    are recorded too (as a real provider's are; without prices only a batch's are). Its answers are
    counted (spent(), a run's meter) but never written to the usage ledger: played answers in a script
    outside the tests went into the real one once (2026-10-24)."""

    kind = "scripted"
    ledgered = False

    def __init__(self, answers, name="scripted", batches=False, polls=1, never=False, caches=False, prices=None):
        super().__init__(name, dict({"model": "scripted", "batch_poll": 0, "cancel_wait": 0}, **(prices or {})))
        self.answers, self.asked, self.temperatures = answers, [], []
        self.can_batch, self.polls, self.never, self.caches = batches, polls, never, caches
        self.submitted, self.looks, self.cancelled = [], {}, []
        self.priced = prices is not None

    def cacheable(self, system, content):
        """With caches: a question with a part marked for the cache would be kept (the stand-in for a
        prompt cache's minimum met)."""
        return self.caches and isinstance(content, list) and any(part.get("cache") for part in content)

    def answer(self, messages):
        if callable(self.answers):
            return self.answers(messages)
        if not self.answers:
            raise ProviderError("scripted: no answer left")
        return self.answers.pop(0)

    def _ask(self, system, messages, temperature=None):
        self.asked.append((system, list(messages)))
        self.temperatures.append(temperature)
        answer = self.answer(messages)
        if isinstance(answer, dict):
            self.last = {"finish": answer.get("finish", "stop"), "tokens": answer.get("tokens"),
                         "reasoning": answer.get("reasoning", 0)}
            text = answer.get("text", "")
        else:
            self.last = {"finish": "stop", "tokens": None, "reasoning": 0}
            text = answer
        if self.priced:                     # (as a provider with prices: its tokens counted and recorded)
            played = answer if isinstance(answer, dict) else {}
            self.last.update({"input": played.get("input", 10), "tokens": played.get("tokens", 5)})
            self.record()
        return text

    def params(self, system, messages, temperature=None):
        return {"system": system, "messages": list(messages), "temperature": temperature}

    def batch_submit(self, requests):
        bid = "batch_%d" % len(self.submitted)
        results = {}
        for cid, params in requests:
            self.asked.append((params["system"], params["messages"]))
            self.temperatures.append(params["temperature"])
            answer = self.answer(params["messages"])
            if isinstance(answer, dict) and "error" in answer:
                results[cid] = {"type": "errored", "error": {"type": "error", "error": answer["error"]}}
                continue
            text = answer.get("text", "") if isinstance(answer, dict) else answer
            finish = answer.get("finish", "end_turn") if isinstance(answer, dict) else "end_turn"
            played = answer if isinstance(answer, dict) else {}
            results[cid] = {"type": "succeeded", "message": {
                "content": [{"type": "text", "text": text}], "stop_reason": finish,
                "usage": {"input_tokens": played.get("input", 10), "output_tokens": played.get("tokens") or 5}}}
        self.submitted.append({"id": bid, "requests": [(c, p) for c, p in requests], "results": results})
        self.looks[bid] = 0
        return {"id": bid, "processing_status": "in_progress"}

    def batch_state(self, batch_id):
        self.looks[batch_id] += 1
        ended = not self.never and (self.looks[batch_id] >= self.polls or batch_id in self.cancelled)
        results = next(b for b in self.submitted if b["id"] == batch_id)["results"]
        kinds = collections.Counter(r["type"] for r in results.values())
        counts = {"processing": 0 if ended else len(results), "succeeded": kinds["succeeded"] if ended else 0,
                  "errored": kinds["errored"] if ended else 0, "canceled": 0, "expired": 0}
        return {"id": batch_id, "processing_status": "ended" if ended else "in_progress", "request_counts": counts}

    def batch_cancel(self, batch_id):
        self.cancelled.append(batch_id)
        return {"id": batch_id, "processing_status": "canceling"}

    def batch_results(self, batch):
        got = next(b for b in self.submitted if b["id"] == batch["id"])["results"]
        if batch["id"] in self.cancelled:
            return {c: {"type": "canceled"} for c in got}
        return got

    def take(self, reply, mode=None):
        self.last = {"finish": reply.get("stop_reason"), "tokens": reply["usage"]["output_tokens"],
                     "input": reply["usage"]["input_tokens"], "reasoning": 0}
        self.record(mode)
        return "".join(b.get("text", "") for b in reply["content"])


class Providers:
    """Providers for questions that threads ask side by side: take() hands out one that no question
    being asked holds (a free one, else a new one that make() makes), give() takes it back, spent() sums
    what every one made spent; batcher() is the one that asks Message Batches (made once, None when the
    provider cannot). At most as many are made as questions are asked at once. Given a provider instead
    of a function that makes one, that provider is the only one (limit 1: one question at a time, since
    a provider keeps how its last answer ended)."""

    def __init__(self, provider):
        import threading
        self.lock, self._batcher = threading.Lock(), None
        if callable(provider):
            self.make, self.free, self.made, self.limit = provider, [], [], None
        else:
            self.make, self.free, self.made, self.limit = None, [provider], [provider], 1

    def take(self):
        with self.lock:
            if self.free:
                return self.free.pop()
            p = self.make()
            self.made.append(p)
            return p

    def give(self, p):
        with self.lock:
            self.free.append(p)

    def batcher(self):
        if self._batcher is None:
            p = self.take()
            self._batcher = p if getattr(p, "can_batch", False) else False
            if not self._batcher or self.limit == 1:
                self.give(p)                # (the only one: it asks one at a time too, never at once)
        return self._batcher or None

    def spent(self):
        """What the providers made spent together: answers (batch: of them through Message Batches),
        tokens in (cache_read, cache_write) and out, the costs of those that have prices (None where none
        has)."""
        return summed(p.spent() for p in self.made)


def summed(spents):
    """What several providers spent together ([Provider.spent()]): answers (batch: of them through
    Message Batches), tokens in (cache_read, cache_write: of them read from and written to a prompt
    cache) and out, the costs of those that have prices (None where none has)."""
    spent = {"answers": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "batch": 0, "cost": None}
    for sp in spents:
        for key in ("answers", "input", "output", "cache_read", "cache_write", "batch"):
            spent[key] += sp.get(key) or 0
        if sp.get("cost") is not None:
            spent["cost"] = (spent["cost"] or 0.0) + sp["cost"]
    return spent


# ---- a run's meter: what it spends, what it may still ask (a budget), and its events ----

def usd(x):
    """An amount of US dollars as text: to the cent where that is exact, else to a hundredth of a cent
    (a budget of $0.0075 is not $0.01)."""
    return "$%.2f" % x if abs(x * 100 - round(x * 100)) < 1e-6 else "$%.4f" % x


def kind_of(provider, system):
    """The kind of a question, for the budget's estimate of its answer's length: a digest of the
    provider's model, whether it asks without reasoning (quick) and the question's instructions."""
    import hashlib
    text = "%s\0%s\0%s" % (provider.model, bool(getattr(provider, "quick", False)), system or "")
    return hashlib.sha1(text.encode()).hexdigest()[:10]


class Ticket:
    """A question a Meter admitted: its estimate (US dollars, 0 without prices) is reserved until its
    answer is recorded, or the question ends without one (a provider's error, a batch's result that is
    not an answer). Entered (with) around the asking, so that the answer's record() in this thread
    finds it; leaving it releases what its answer did not take."""

    def __init__(self, meter, kind, estimate, turn, chars=0):
        self.meter, self.kind, self.estimate, self.turn, self.chars = meter, kind, estimate, turn, chars
        self.done, self.outer = False, None

    def __enter__(self):
        self.outer = getattr(_asking, "ticket", None)
        _asking.ticket = self
        return self

    def __exit__(self, *exc):
        _asking.ticket = self.outer
        self.meter.release(self)
        return False


class Meter:
    """What one run spends and may still ask, and the run's events (dread pilot: every run of it, its
    --budget). metered() installs it for the run (METER); every provider of the process then asks through
    it (Provider.ask: admit; ask_batch: admit_many) and counts its answers to it (Provider.record:
    answered). Threads share it (a lock).

    The spend is the sum of the run's answers, each priced as the usage ledger prices it (answer_cost: a
    Message Batch's at the batch rate, a prompt cache's tokens at theirs), counted as it is recorded: what
    this run spent, whatever runs beside it (the ledger is shared). A question is admitted when its
    estimate (estimate()) fits what is left of the budget after the spend and the estimates of the
    questions being answered (reserved): admit() raises BudgetReached otherwise; admit_many() admits a
    batch's questions in their order while they fit and none after the first that does not. The first
    question so held back marks the budget reached (reached: its words, and a "budget" event): the pilot
    then asks nothing new and ends with "budget reached"; a conversation already going still asks a next
    question that fits. admit_many marks the budget reached only when it admits none of a batch's
    questions: ask_batch weighs the rest again once those admitted are answered (the estimates are better
    then). fits() and hold() let a caller weigh a group of questions before it asks
    any (a function's samples, a wave of a batch), so that it asks a group whole or not at all.

    estimate(): a question's cost before it is asked, at the provider's prices (answer_cost; mode "batch"
    for the batch rate; no prompt cache assumed, neither its saving nor what writing it costs more): its
    input from its text (the system part and every message) at the characters a token its kind's answers
    took in this run (the provider's input tokens against the questions' characters), else in earlier runs
    (prior), else CHARS_PER_TOKEN (or the provider's chars_per_token); its output from the last OUT_WINDOW
    answers of its kind in this run (kind_of: the model, quick, the instructions), the larger of their
    mean and their tokens out a token in times this question's input (an answer that names a function's
    variables grows with the function: on FreeRTOS a batch weighed at the mean of the first, smallest
    functions' answers cost 1.46 times its estimate, 2026-10-24), else from what earlier runs' answers of
    the kind took (prior: {kind: {"out": tokens, "chars": characters a token, "per_in": tokens out a token
    in}}, outputs()), else OUT_TOKENS, at most the provider's max_tokens. None for a provider without prices:
    its questions cannot be weighed, and a budget holds none of them back (dread pilot refuses --budget for
    such a provider). What the estimate does not establish: an answer's real length (a question asks
    again, a model reasons longer); the run's spend can pass the budget by what its admitted questions
    cost beyond their estimates.

    emit(kind, **fields) is the run's event writer (dread.pilotlog RunLog.event): answered() makes each
    answer an event ("answer": its number in the run, the provider, mode, tokens, cost, the spend so far,
    its estimate and turn), ask_batch and converse_all theirs ("batch", "round"), the budget its own."""

    def __init__(self, budget=None, emit=None, prior=None):
        self.budget = None if budget is None else float(budget)
        self.emit = emit or (lambda kind, **fields: None)
        self.prior = dict(prior or {})
        self.lock = threading.Lock()
        self.spent = self.reserved = 0.0
        self.answers = self.batch = self.asked = self.held = 0
        self.tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.recent = {}                    # {kind: the last OUT_WINDOW answers' tokens out}
        self.kinds = {}                     # {kind: [answers, tokens out, characters in, tokens in]} (outputs())
        self.reached = None                 # the words of the first question held back

    def prior_of(self, kind):
        """(tokens out, characters a token, tokens out a token in) of a kind from earlier runs, each None
        where they say none."""
        got = self.prior.get(kind)
        if isinstance(got, dict):
            return got.get("out"), got.get("chars"), got.get("per_in")
        return (got, None, None) if isinstance(got, (int, float)) else (None, None, None)

    def output(self, provider, system, tokens_in=None):
        """The tokens out estimate() takes for an answer of this kind to a question of tokens_in tokens
        (see the class): the larger of its kind's mean and its kind's tokens out a token in times
        tokens_in."""
        kind = kind_of(provider, system)
        with self.lock:
            recent = self.recent.get(kind)
            if recent:
                mean = sum(out for _, out in recent) / len(recent)
                given = sum(i for i, _ in recent if i)
                per_in = sum(out for i, out in recent if i) / given if given else None
            else:
                mean, _, per_in = self.prior_of(kind)
                mean = float(mean or OUT_TOKENS)
        tokens = max(mean, per_in * tokens_in) if per_in and tokens_in else mean
        most = provider.config.get("max_tokens")
        return min(tokens, float(most)) if most else tokens

    def chars_per_token(self, provider, system):
        """The characters a token estimate() takes for a question of this kind (see the class)."""
        kind = kind_of(provider, system)
        with self.lock:
            seen = self.kinds.get(kind)
            if seen and seen[2] and seen[3]:
                return seen[2] / seen[3]
        return float(self.prior_of(kind)[1] or provider.config.get("chars_per_token", CHARS_PER_TOKEN))

    def weigh(self, provider, system, messages, mode=None):
        """(estimate or None, kind, characters, turn) of a question (see the class)."""
        chars = len(system or "") + sum(len(content_text(m.get("content"))) for m in messages)
        turn = sum(1 for m in messages if m.get("role") == "user")
        kind = kind_of(provider, system)
        if prices_of(provider.config, provider.kind) is None:
            return None, kind, chars, turn
        tokens_in = chars / self.chars_per_token(provider, system)
        return (answer_cost(provider.config, provider.kind, tokens_in, self.output(provider, system, tokens_in),
                            mode=mode), kind, chars, turn)

    def estimate(self, provider, system, messages, mode=None):
        """A question's estimated cost in US dollars (see the class), or None without prices."""
        return self.weigh(provider, system, messages, mode)[0]

    def _fits(self, cost):
        return self.budget is None or cost is None or self.spent + self.reserved + cost <= self.budget + 1e-12

    def fits(self, cost):
        """Whether questions estimated at cost (US dollars) fit what is left of the budget now."""
        with self.lock:
            return self._fits(cost)

    def left(self):
        """What is left of the budget after the spend and the questions being answered, or None."""
        with self.lock:
            return None if self.budget is None else self.budget - self.spent - self.reserved

    def _hold(self, cost, questions, what):
        """Note questions held back (under the lock); returns the words for them and whether they are
        the first (the caller emits the event outside the lock)."""
        self.held += questions
        words = "not asked: %s estimated at $%.4f would take the run's spend ($%.4f, $%.4f more reserved for the " \
                "questions being answered) past its budget (%s)" % (what, cost or 0.0, self.spent, self.reserved,
                                                                  usd(self.budget or 0.0))
        first = self.reached is None
        if first:
            self.reached = words
        return words, first

    def _reached(self, words, cost, questions):
        self.emit("budget", state="reached", budget=self.budget, spent=round(self.spent, 6),
                  reserved=round(self.reserved, 6), estimate=round(cost or 0.0, 6), questions=questions, words=words)

    def hold(self, cost, questions=1, what="the next questions"):
        """Hold back questions a caller weighed (fits) and will not ask: the budget is reached. Returns
        the words."""
        with self.lock:
            words, first = self._hold(cost, questions, what)
        if first:
            self._reached(words, cost, questions)
        return words

    def admit(self, provider, system, messages, mode=None):
        """A Ticket for a question about to be asked; BudgetReached when its estimate does not fit."""
        cost, kind, chars, turn = self.weigh(provider, system, messages, mode)
        ticket = Ticket(self, kind, cost or 0.0, turn, chars)
        with self.lock:
            if self._fits(cost):
                self.reserved += ticket.estimate
                self.asked += 1
                return ticket
            words, first = self._hold(cost, 1, "the question")
        if first:
            self._reached(words, cost, 1)
        raise BudgetReached(words)

    def admit_many(self, provider, questions, mode="batch"):
        """([Ticket or None], words): questions ([(system, messages, temperature)]) admitted in their
        order while their estimates fit, None for each from the first that does not (words: why, or
        None when all fit). Only when none fits are they held back (the budget reached): the caller
        weighs the rest again once the admitted ones are answered (ask_batch)."""
        weighed = [self.weigh(provider, system, messages, mode) for system, messages, _ in questions]
        tickets, words, first, refused, closed = [], None, False, 0.0, False
        with self.lock:
            for cost, kind, chars, turn in weighed:
                closed = closed or not self._fits(cost)
                if closed:
                    tickets.append(None)
                    refused += cost or 0.0
                    continue
                tickets.append(Ticket(self, kind, cost or 0.0, turn, chars))
                self.reserved += cost or 0.0
                self.asked += 1
            if closed:
                what = "%d questions of a batch" % tickets.count(None)
                if any(tickets):
                    words = "not asked yet: %s estimated at $%.4f do not fit what is left of the budget" % (
                        what, refused)
                else:
                    words, first = self._hold(refused, tickets.count(None), what)
        if first:
            self._reached(words, refused, tickets.count(None))
        return tickets, words

    def release(self, ticket):
        """A question that ended without an answer recorded (or after it): its estimate is no longer
        reserved."""
        with self.lock:
            if not ticket.done:
                ticket.done = True
                self.reserved -= ticket.estimate

    def answered(self, provider, info, mode, dollars):
        """An answer a provider records (Provider.record): its cost into the spend, its ticket's
        estimate no longer reserved, its tokens out into its kind's mean; numbered in the run (info's
        "answer") and made an event, under the meter's lock, so that the answers' events come in the
        order of their numbers and their spends (emit takes the run log's own lock inside this one,
        never the other way round)."""
        ticket = getattr(_asking, "ticket", None)
        if ticket is not None and ticket.meter is not self:
            ticket = None
        with self.lock:
            self.answers += 1
            n = self.answers
            self.batch += mode == "batch"
            self.spent += dollars or 0.0
            for key, field in (("input", "input"), ("output", "tokens"), ("cache_read", "cache_read"),
                               ("cache_write", "cache_write")):
                self.tokens[key] += info.get(field) or 0
            fields = {"n": n, "provider": provider.name, "model": provider.model, "mode": mode or "",
                      "input": info.get("input"), "output": info.get("tokens"), "finish": info.get("finish"),
                      "cost": None if dollars is None else round(dollars, 7), "spent": round(self.spent, 7)}
            if info.get("cache_read") or info.get("cache_write"):
                fields.update(cache_read=info.get("cache_read") or 0, cache_write=info.get("cache_write") or 0)
            if ticket is not None and not ticket.done:
                ticket.done = True
                self.reserved -= ticket.estimate
                fields.update(estimate=round(ticket.estimate, 7), turn=ticket.turn)
                seen = self.kinds.setdefault(ticket.kind, [0, 0, 0, 0, 0, 0])
                if info.get("tokens") is not None:
                    self.recent.setdefault(ticket.kind, collections.deque(maxlen=OUT_WINDOW)).append(
                        (info.get("input") or 0, info["tokens"]))
                    seen[0] += 1
                    seen[1] += info["tokens"]
                    if info.get("input"):
                        seen[4] += info["input"]
                        seen[5] += info["tokens"]
                if info.get("input") and ticket.chars:
                    seen[2] += ticket.chars
                    seen[3] += info["input"]
            info["answer"] = n
            self.emit("answer", **fields)

    def outputs(self):
        """{kind: {"out": tokens out of an answer on average, "chars": characters of a question a token in,
        "per_in": tokens out a token in}} of this run's answers (the next run's prior; each left out where
        the answers did not tell it)."""
        with self.lock:
            out = {}
            for kind, (n, total, chars, tokens, given, answered) in self.kinds.items():
                told = {"out": round(total / n, 1)} if n else {}
                if chars and tokens:
                    told["chars"] = round(chars / tokens, 3)
                if given:
                    told["per_in"] = round(answered / given, 4)
                if told:
                    out[kind] = told
            return out

    def totals(self):
        """What the run spent: answers (batch: of them in Message Batches), tokens, cost (US dollars),
        questions admitted (asked) and held back (held)."""
        with self.lock:
            return dict(self.tokens, answers=self.answers, batch=self.batch, cost=round(self.spent, 7),
                        asked=self.asked, held=self.held)


class metered:
    """A context in which meter is the run's (METER): every provider of the process asks through it
    (see Meter). The meter before it comes back at its end."""

    def __init__(self, meter):
        self.meter, self.before = meter, None

    def __enter__(self):
        global METER
        self.before, METER = METER, self.meter
        return self.meter

    def __exit__(self, *exc):
        global METER
        METER = self.before
        return False


def emit(kind, **fields):
    """An event of the run's meter (METER), if there is one."""
    meter = METER
    if meter is not None:
        meter.emit(kind, **fields)


# ---- conversations: one logic for a question asked alone and for one asked in a batch ----
#
# A conversation is a generator: it yields each question it asks as (system, messages, temperature)
# and is sent back (answer, last, error): the answer's text (None after an error), how it ended
# (Provider.last: finish, tokens, ...) and the provider's error (an exception) or None; what it returns
# is the conversation's result. converse() asks its questions one after another through
# Provider.ask; converse_all() asks the questions of many at once, as Message Batches where a round
# is large enough. Either way the conversation's code decides what an answer means and what goes back,
# so an answer of a batch is handled exactly as an answer asked alone.

def converse(provider, conversation, first=None):
    """The result of a conversation, its questions asked one after another through provider.ask (first:
    its first question when the generator has already yielded it). provider.last is cleared before each
    question, so that a provider's error leaves no finish of the question before."""
    try:
        request = next(conversation) if first is None else first
        while True:
            system, messages, temperature = request
            provider.last = {}
            try:
                answer = provider.ask(system, messages) if temperature is None else \
                    provider.ask(system, messages, temperature=temperature)
                reply = (answer, dict(getattr(provider, "last", {}) or {}), None)
            except Exception as e:
                reply = (None, {}, e)
            request = conversation.send(reply)
    except StopIteration as stop:
        return stop.value


class thread_pool:
    """A pool of worker threads (concurrent.futures.ThreadPoolExecutor) for questions. When its block
    ends by an exception (SIGTERM's SystemExit under ending_on_signals, KeyboardInterrupt) the questions
    not begun are cancelled and those being asked are waited for at most stop_wait seconds (STOP_WAIT),
    their threads going on in the background (an answer they get is still recorded), so that a stopped
    run asks nothing more and gets to its end event in time: a plain ThreadPoolExecutor's block runs every
    question queued before it ends, and waits for all of them, where dread gui stops a job with SIGTERM
    and kills it 30 s later. Ended normally, it waits for everything, as a ThreadPoolExecutor does."""

    def __init__(self, workers, stop_wait=None):
        import concurrent.futures
        begun = self.begun = []

        class Pool(concurrent.futures.ThreadPoolExecutor):
            def submit(self, fn, /, *args, **kwargs):
                future = super().submit(fn, *args, **kwargs)
                if len(begun) > 1000:       # (those done no longer matter)
                    begun[:] = [f for f in begun if not f.done()]
                begun.append(future)
                return future
        self.pool = Pool(max_workers=max(1, workers))
        self.stop_wait = STOP_WAIT if stop_wait is None else stop_wait

    def __enter__(self):
        return self.pool

    def __exit__(self, kind, value, traceback):
        import concurrent.futures
        if kind is None:
            self.pool.shutdown(wait=True)
            return False
        self.pool.shutdown(wait=False, cancel_futures=True)
        concurrent.futures.wait([f for f in self.begun if not f.done()], timeout=self.stop_wait)
        return False


class ending_on_signals:
    """A context in which SIGTERM and SIGHUP end the program as an exception does (SystemExit, status
    128 plus the signal), so that what is in flight is cleaned up: ask_batch cancels the batches it is
    waiting for (a batch left running is billed for every question it answers). The main thread only
    (elsewhere it does nothing); the handlers before it come back at its end. (SIGINT is left as it is:
    Python raises KeyboardInterrupt for it, unless the program was started in the background by a
    shell without job control, which makes it ignore SIGINT: then only SIGTERM reaches it.)"""

    SIGNALS = ("SIGTERM", "SIGHUP")

    def __enter__(self):
        import signal
        import threading
        self.old = {}
        if threading.current_thread() is not threading.main_thread():
            return self
        mine = os.getpid()

        def end(signum, frame):
            if os.getpid() != mine:         # (a forked worker inherits the handler: it ends as it would have.
                signal.signal(signum, signal.SIG_DFL)   # A pool ends its workers with SIGTERM: raising
                os.kill(os.getpid(), signum)            # there left one waiting on a lock, and its parent
                return                                  # waiting for it, 2026-10-24)
            raise SystemExit(128 + signum)
        for name in self.SIGNALS:
            number = getattr(signal, name, None)
            if number is not None:
                try:
                    self.old[number] = signal.signal(number, end)
                except (ValueError, OSError):
                    pass
        return self

    def __exit__(self, *exc):
        import signal
        for number, handler in self.old.items():
            signal.signal(number, handler)
        return False


RETRY_ERRORS = ("api_error", "overloaded_error", "rate_limit_error", "timeout_error")   # a batch's errored results
                                                                                        # asked again one by one


def ask_batch(provider, questions, out=print):
    """[(answer, last, error)] of questions ([(system, messages, temperature)]), in their order, asked as
    Message Batches of provider (provider.can_batch): as few batches as BATCH_REQUESTS and BATCH_BYTES
    allow (batch_requests, batch_bytes), polled every BATCH_POLL seconds (batch_poll) until every one
    has ended, the answers matched back by custom_id and recorded as a batch's (price_batch). A batch
    that has not ended after BATCH_WAIT seconds (batch_wait) is cancelled (what it answered by then is
    taken, the rest fails with an error that says so); so is every batch not ended when the waiting is
    interrupted. An errored result of the server's kind (RETRY_ERRORS) is asked again through
    provider.ask, as a question asked alone would have been; other errored, cancelled or expired
    results fail with the API's words.

    With a run's meter (METER) the questions are admitted first (Meter.admit_many, at the batch rate),
    in their order while they fit the budget; those that do not are weighed again once the admitted
    ones are answered (the estimates know the answers' lengths then) and go in a batch of their own if
    any fits, until none fits: those fail with BudgetReached. The meter gets the batches' events:
    "batch" submitted (its id, requests, estimate), looked at (its status and the API's request counts,
    when they changed or BATCH_LOOK seconds after the last such event), ended (succeeded, errored,
    cancelled, expired), cancelled (why), or failed to submit."""
    import contextlib
    import time
    config = provider.config
    meter = METER
    emit = meter.emit if meter is not None else (lambda kind, **fields: None)
    poll, wait = float(config.get("batch_poll", BATCH_POLL)), float(config.get("batch_wait", BATCH_WAIT))
    most, room = int(config.get("batch_requests", BATCH_REQUESTS)), int(config.get("batch_bytes", BATCH_BYTES))
    requests = [("q%d" % k, provider.params(*q)) for k, q in enumerate(questions)]
    position = {cid: k for k, (cid, _) in enumerate(requests)}
    replies, tickets, again, started, submitted = {}, {}, [], time.time(), []

    def go(asked):
        """Submit the requests of asked (custom_ids) as batches, wait for them, take their results into
        replies; returns the custom_ids to ask again alone (RETRY_ERRORS)."""
        chunks, size = [[]], 0
        for cid in asked:
            params = requests[position[cid]][1]
            length = len(json.dumps(params)) + len(cid) + 40
            if chunks[-1] and (len(chunks[-1]) >= most or size + length > room):
                chunks.append([])
                size = 0
            chunks[-1].append((cid, params))
            size += length
        batches, seen, retry, began = [], {}, [], time.time()
        for chunk in chunks:
            if not chunk:
                continue
            try:
                batch = provider.batch_submit(chunk)
            except ProviderError as e:
                for cid, _ in chunk:
                    replies[cid] = (None, {}, e)
                emit("batch", state="failed", requests=len(chunk), error=str(e)[:300])
                continue
            batches.append([batch, chunk])
            submitted.append(batch)
            seen[batch.get("id")] = (time.time(), None, time.time())
            emit("batch", state="submitted", id=batch.get("id"), requests=len(chunk),
                 estimate=round(sum(tickets[cid].estimate for cid, _ in chunk if cid in tickets), 7))
        if batches:
            out("dread: %d questions to %s as %d Message Batch%s (%s), polled every %.0f s for %.0f s at most" % (
                sum(len(c) for _, c in batches), provider.label(), len(batches), "" if len(batches) == 1 else "es",
                ", ".join(b["id"] for b, _ in batches), poll, wait))

        def looked(batch):
            """The look's event, when it shows something new or BATCH_LOOK seconds have passed."""
            bid = batch.get("id")
            submitted_at, last, at = seen.get(bid, (began, None, began))
            now_ = time.time()
            shown = (batch.get("processing_status"), json.dumps(batch.get("request_counts") or {}, sort_keys=True))
            if shown != last or now_ - at >= BATCH_LOOK:
                seen[bid] = (submitted_at, shown, now_)
                emit("batch", state="looked", id=bid, status=batch.get("processing_status"),
                     counts=batch.get("request_counts"), age=round(now_ - submitted_at, 1))
        late = None
        try:
            while any(b.get("processing_status") != "ended" for b, _ in batches):
                if time.time() - began > wait:
                    late = [b["id"] for b, _ in batches if b.get("processing_status") != "ended"]
                    break
                time.sleep(poll)
                for pair in batches:
                    if pair[0].get("processing_status") != "ended":
                        try:
                            pair[0] = provider.batch_state(pair[0]["id"])
                            looked(pair[0])
                        except ProviderError as e:     # (a look that fails is tried again at the next)
                            out("dread: a look at batch %s failed: %s" % (pair[0]["id"], e))
        except BaseException:                   # (interrupted: the batches are not left running at a price)
            for b, _ in batches:
                if b.get("processing_status") != "ended":
                    try:
                        provider.batch_cancel(b["id"])
                        emit("batch", state="cancelled", id=b["id"], why="interrupted")
                    except ProviderError:
                        pass
            raise
        if late:
            for pair in batches:
                if pair[0]["id"] in late:
                    try:
                        provider.batch_cancel(pair[0]["id"])
                        emit("batch", state="cancelled", id=pair[0]["id"], why="batch_wait")
                    except ProviderError as e:
                        out("dread: cancelling batch %s failed: %s" % (pair[0]["id"], e))
            until = time.time() + float(config.get("cancel_wait", CANCEL_WAIT))
            while any(b.get("processing_status") != "ended" for b, _ in batches if b["id"] in late) and \
                    time.time() < until:
                time.sleep(poll)
                for pair in batches:
                    if pair[0]["id"] in late and pair[0].get("processing_status") != "ended":
                        try:
                            pair[0] = provider.batch_state(pair[0]["id"])
                        except ProviderError:
                            pass
        for b, chunk in batches:
            try:
                got = provider.batch_results(b) if b.get("processing_status") == "ended" else {}
            except ProviderError as e:
                got = {cid: {"type": "errored", "error": {"message": "its results could not be read: %s" % e}}
                       for cid, _ in chunk}
            kinds = collections.Counter()
            for cid, _ in chunk:
                result = got.get(cid) or {}
                kind = result.get("type")
                kinds[kind or "none"] += 1
                if kind == "succeeded":
                    try:
                        with tickets.get(cid) or contextlib.nullcontext():     # (its answer finds its ticket)
                            text = provider.take(result.get("message") or {}, mode="batch")
                        replies[cid] = (text, dict(provider.last), None)
                    except ProviderError as e:
                        replies[cid] = (None, {}, e)
                    continue
                error = result.get("error") or {}
                while isinstance(error.get("error"), dict):     # ({"type": "error", "error": {"type": ..., ...}})
                    error = error["error"]
                if kind == "errored" and error.get("type") in RETRY_ERRORS:
                    retry.append(cid)
                    continue
                if b["id"] in (late or ()):
                    why = "the batch %s did not end within %.0f s (batch_wait); it was cancelled, a question it had " \
                          "not answered is not billed" % (b["id"], wait)
                elif kind == "errored":
                    why = "the batch %s: %s" % (b["id"], error.get("message") or error.get("type") or "errored")
                elif kind in ("canceled", "expired"):
                    why = "the batch %s: the question was %s before the model answered it" % (b["id"], kind)
                else:
                    why = "the batch %s: no result for the question" % b["id"]
                replies[cid] = (None, {}, ProviderError("%s: %s" % (provider.name, why)))
            emit("batch", state="ended", id=b.get("id"), requests=len(chunk), succeeded=kinds["succeeded"],
                 errored=kinds["errored"], canceled=kinds["canceled"], expired=kinds["expired"],
                 missing=kinds["none"], seconds=round(time.time() - seen.get(b.get("id"), (began,))[0], 1))
        if late:
            out("dread: %d of %d batches did not end within %.0f s and were cancelled (%s): their unanswered questions "
                "fail; batch_wait in llm.toml lets a batch take longer" % (len(late), len(batches), wait,
                                                                           ", ".join(late)))
        return retry

    rest = [cid for cid, _ in requests]
    try:
        while rest:
            now, later = rest, []
            if meter is not None:           # (those that fit now; the rest weighed again after them)
                admitted, words = meter.admit_many(provider, [questions[position[cid]] for cid in rest], "batch")
                now = [cid for cid, ticket in zip(rest, admitted) if ticket is not None]
                later = [cid for cid, ticket in zip(rest, admitted) if ticket is None]
                tickets.update((cid, ticket) for cid, ticket in zip(rest, admitted) if ticket is not None)
                if not now:
                    for cid in later:
                        replies[cid] = (None, {}, BudgetReached(words))
                    break
            again += go(now)
            for cid in now:                 # (what their answers did not take, before the rest is weighed)
                if cid in tickets:
                    meter.release(tickets[cid])
            rest = later
    finally:                                # (what no answer took is no longer reserved)
        for ticket in tickets.values():
            meter.release(ticket)
    for cid in again:                       # (as a question asked alone, which the provider asks again itself)
        provider.last = {}
        system, messages, temperature = questions[position[cid]]
        try:
            text = provider.ask(system, messages) if temperature is None else \
                provider.ask(system, messages, temperature=temperature)
            replies[cid] = (text, dict(getattr(provider, "last", {}) or {}), None)
        except Exception as e:
            replies[cid] = (None, {}, e)
    if submitted:
        out("dread: the batch%s ended after %.0f s: %d answered, %d failed" % (
            "" if len(submitted) == 1 else "es", time.time() - started,
            sum(1 for cid, _ in requests if replies[cid][2] is None), sum(1 for cid, _ in requests
                                                                          if replies[cid][2] is not None)))
    return [replies[cid] for cid, _ in requests]


def converse_all(providers, conversations, batch=0, jobs=1, leads=None, strict=True, out=print):
    """The results of conversations (generators, as converse() drives one), in their order, asked in
    rounds: a round takes the question every conversation that has not ended asks now (a conversation
    with a lead, leads[k] the index of another, begins only once that one has ended: it reads what the
    lead wrote to the provider's prompt cache; with strict False only where questions go one at a time,
    while in a round that goes as a batch it joins its lead, after it in the batch's order, the cache
    then the API's best effort). A round of batch questions or more (batch > 0) whose provider can batch
    (providers.batcher(): providers a Providers, or a provider) goes as Message Batches (ask_batch), each
    answer sent back to its conversation (jobs at a time: a conversation may check its answer, compile
    code); the questions they ask next make the next round. A smaller round, or any round without
    batches, runs each of its conversations to its end, one question after another, jobs at a time,
    each with a provider of providers while it asks (converse). Each round is an event of the run's
    meter ("round": its questions, whether they go as a batch)."""
    providers = providers if isinstance(providers, Providers) else Providers(providers)
    n = len(conversations)
    results, ended, pending = [None] * n, [False] * n, {}
    later = [k for k in range(n) if leads and leads[k] is not None]

    def start(k):
        try:
            pending[k] = next(conversations[k])
        except StopIteration as stop:
            results[k], ended[k] = stop.value, True

    for k in range(n):
        if not (leads and leads[k] is not None):
            start(k)
    while True:
        if not pending:
            ready = [k for k in later if ended[leads[k]]]
            if not ready:
                break
            for k in ready:
                later.remove(k)
                start(k)
            continue
        joining = [] if strict else [k for k in later if leads[k] in pending]
        batcher = providers.batcher() if batch and len(pending) + len(joining) >= batch else None
        if batcher is not None:
            for k in joining:               # (into its lead's batch, after it)
                later.remove(k)
                start(k)
        ks = sorted(pending)
        emit("round", questions=len(ks), batch=batcher is not None)
        if batcher is not None:
            replies = ask_batch(batcher, [pending[k] for k in ks], out=out)

            def send(item):
                k, reply = item
                try:
                    return k, conversations[k].send(reply), None
                except StopIteration as stop:
                    return k, None, stop
            with thread_pool(jobs) as pool:
                for k, request, stop in pool.map(send, zip(ks, replies)):
                    if stop is not None:
                        results[k], ended[k] = stop.value, True
                        del pending[k]
                    else:
                        pending[k] = request
        else:
            def run(k):
                p = providers.take()
                try:
                    return k, converse(p, conversations[k], pending[k])
                finally:
                    providers.give(p)
            with thread_pool(min(jobs, providers.limit or jobs, len(ks))) as pool:
                for k, value in pool.map(run, ks):
                    results[k], ended[k] = value, True
            pending.clear()
        for k in [k for k in later if ended[leads[k]]]:
            later.remove(k)
            start(k)
    return results


KINDS = {"openai": OpenAICompatible, "anthropic": Anthropic, "command": Command}


def usage_summary(month=None, path=None, config_path=None):
    """{provider: {"model", "answers", "input", "output", "cache_read", "cache_write", "batch" (answers of
    Message Batches), "cost" (None without prices)}} of the usage ledger for a month ("YYYY-MM",
    default: this one), each line priced as it came (answer_cost: a batch's at the batch rate, its
    cache's tokens at theirs). A line of before 2026-10-22 has no cache columns, one of before
    2026-10-24 no mode and no hour-long cache writes (USAGE_COLUMNS; docs/formats.md)."""
    import time
    import tomllib
    month = month or time.strftime("%Y-%m")
    specs = {}
    config_path = config_path or os.environ.get("DREAD_LLM") or CONFIG
    if os.path.exists(config_path):
        with open(config_path, "rb") as f:
            specs = tomllib.load(f).get("providers", {})
    out = {}
    path = path or USAGE
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            row = line.rstrip("\n").split("\t")
            if len(row) < 6 or not row[0].startswith(month):
                continue
            row += [""] * (len(USAGE_COLUMNS) - len(row))
            try:
                tokens = [int(row[k] or 0) for k in (3, 4, 6, 7, 9)]
            except ValueError:              # (a line cut off or of another kind)
                continue
            entry = out.setdefault(row[1], {"model": row[2], "answers": 0, "input": 0, "output": 0, "cost": None,
                                            "cache_read": 0, "cache_write": 0, "batch": 0})
            entry["answers"] += 1
            entry["batch"] += row[8] == "batch"
            for key, value in zip(("input", "output", "cache_read", "cache_write"), tokens):
                entry[key] += value
            spec = specs.get(row[1]) or {}
            dollars = answer_cost(spec, spec.get("kind"), *tokens, mode=row[8] or None)
            if dollars is not None:
                entry["cost"] = (entry["cost"] or 0.0) + dollars
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
    """dread llm providers | dread llm ask [--provider NAME] [--system TEXT] TEXT... | dread llm batches
    [--provider NAME] [--limit N] [--cancel ID...]: the configured providers, one question to one of them
    with its answer (to try a provider before a pilot run), or a provider's last Message Batches with
    how they stand (cancelling those named: a run killed outright cannot cancel its own, and a batch
    left running is billed for every question it answers)."""
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
    b = sub.add_parser("batches", help="a provider's last Message Batches, or cancel some")
    b.add_argument("--provider", help="a provider of the configuration (default: its default)")
    b.add_argument("--limit", type=int, default=20, help="batches listed at most (default 20)")
    b.add_argument("--cancel", nargs="+", metavar="ID", help="cancel these batches instead")
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
    if args.action == "batches":
        try:
            provider = load(args.provider)
            if not provider.can_batch:
                print("dread llm: %s has no Message Batches" % provider.label(), file=sys.stderr)
                return 1
            if args.cancel:
                for bid in args.cancel:
                    print("%s %s" % (bid, provider.batch_cancel(bid).get("processing_status", "?")))
                return 0
            for batch in provider.batch_list(args.limit):
                counts = batch.get("request_counts") or {}
                print("%s  %-11s %s  %s" % (batch.get("id"), batch.get("processing_status"),
                                            (batch.get("created_at") or "")[:19],
                                            ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items()) if v)))
        except ProviderError as e:
            print("dread llm: %s" % e, file=sys.stderr)
            return 1
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
