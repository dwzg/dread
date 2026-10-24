"""dread llm set and dread llm key: the language-model providers set up from the command line, and from
dread gui's Setup page, which calls the same functions.

    dread llm set NAME [--kind openai|anthropic|command] [--model MODEL] [--url URL] [--price-in USD]
                  [--price-out USD] [--cache-min TOKENS] [--batch | --no-batch] [--key-env VARIABLE]
                  [--key-file PATH] [--default] [--unset FIELD...]

adds a provider to llm.toml ($DREAD_LLM, else ~/.config/dread/llm.toml; dread.llm) or changes its
fields. The file is edited line by line, not written anew from a parsed copy: a field's line gets its
new value in place (a comment behind it kept), a new field goes below the provider's last field, a new
provider to the end of the file, `default` below the other keys at the top; every other line (comments,
blank lines, the other providers, fields this does not know) stays as it was. The result has to parse
(tomllib) and read back as asked, with every other provider and key as before, before it replaces the
file (written whole beside it and renamed into place, its mode kept). A provider written as an inline
table or with dotted keys is refused (edit it by hand); so is a new command provider, whose command
line is written by hand: set changes the other fields of one. FIELDS says what each field takes.

    dread llm key NAME [--file PATH]

reads a key from standard input (without echo from a terminal) and writes it into the provider's key
file: --file, else the key_file the provider names, else keys/NAME beside llm.toml
(~/.config/dread/keys/NAME). The file gets mode 600 (written beside it and renamed into place), a
directory made for it 700. A place inside a git work tree or in a directory others may write to is
refused, and the file written has to pass the checks a request makes (dread.llm check_key_file); then
the provider's key_file names it. The key is never printed, logged or kept anywhere else: what is
printed says where it went.
"""
import json
import math
import os
import re
import sys
import tomllib

from dread import llm

ACTIONS = ("set", "key")
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")         # a provider's name: a bare TOML key
ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
URL = re.compile(r"https?://[^\s\"'<>]+\Z")
KEY_MAX = 4096                                       # characters a key may have
# The fields set writes, with the type each takes, in the order a new provider's lines get them.
FIELDS = {"kind": str, "url": str, "model": str, "key_env": str, "key_file": str, "price_in": float,
          "price_out": float, "cache_min": int, "batch": bool}
REQUIRED = ("kind", "model")                         # (a new provider needs them; they cannot be unset)


class ConfigError(ValueError):
    """A change llm.toml refuses (the message says why; it never holds a key)."""


# ---- reading llm.toml as statements ----

def _line_end(text, i):
    nl = text.find("\n", i)
    return len(text) if nl < 0 else nl + 1


def _key_end(text, i, stop):
    """Where `stop` (] or =) comes outside quotes, from i on in one line."""
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                if c == '"' and text[j] == "\\":
                    j += 1
                j += 1
            i = j + 1
            continue
        if c == stop:
            return i
        if c == "\n":
            break
        i += 1
    raise ConfigError("llm.toml: a line that is neither a table, a key nor a comment")


def _parts(key, header=False):
    """The parts of a dotted TOML key, decoded by tomllib itself."""
    data = tomllib.loads(("[%s]" % key) if header else (key + " = 0"))
    parts = []
    while isinstance(data, dict) and len(data) == 1:
        (k, data), = data.items()
        parts.append(k)
    return tuple(parts)


def _value_end(text, i):
    """(end of the value that starts at i, before blanks and a comment behind it; the end of its last
    line). The text is TOML that tomllib took: strings, arrays and inline tables may span lines."""
    n, depth, last = len(text), 0, i
    while i < n:
        c = text[i]
        if text.startswith('"""', i) or text.startswith("'''", i):
            quote, j = text[i:i + 3], i + 3
            while True:
                k = text.index(quote, j)
                if quote == '"""':
                    slashes = 0
                    while text[k - 1 - slashes] == "\\":
                        slashes += 1
                    if slashes % 2:
                        j = k + 1
                        continue
                while text.startswith(quote[0], k + 3):     # (up to two quotes before the closing ones)
                    k += 1
                break
            i = last = k + 3
            continue
        if c in "\"'":
            j = i + 1
            while text[j] != c:
                j += 2 if c == '"' and text[j] == "\\" else 1
            i = last = j + 1
            continue
        if c == "#":
            i = text.find("\n", i)
            i = n if i < 0 else i
            continue
        if c == "\n":
            if depth <= 0:
                return last, i + 1
            i += 1
            continue
        if c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        if c not in " \t\r":
            last = i + 1
        i += 1
    return last, n


def statements(text):
    """The statements of a TOML text: [{"kind": "table"|"array"|"kv", "table": the parts of the table a
    key is in (None under an array of tables), "parts": the key's or header's parts, "start", "end" (its
    lines), "value": (start, end) of a key's value}]."""
    out, i, n, table = [], 0, len(text), ()
    while i < n:
        j = i
        while j < n and text[j] in " \t":
            j += 1
        if j >= n:
            break
        if text[j] in "\r\n#":
            i = _line_end(text, j)
            continue
        if text[j] == "[":
            array = text.startswith("[[", j)
            k = _key_end(text, j + (2 if array else 1), "]")
            parts = _parts(text[j + (2 if array else 1):k], header=True)
            end = _line_end(text, k)
            out.append({"kind": "array" if array else "table", "parts": parts, "start": i, "end": end, "table": None})
            table = None if array else parts
            i = end
            continue
        k = _key_end(text, j, "=")
        v = k + 1
        while v < n and text[v] in " \t":
            v += 1
        value_end, end = _value_end(text, v)
        out.append({"kind": "kv", "table": table, "parts": _parts(text[j:k]), "start": i, "end": end,
                    "value": (v, value_end)})
        i = end
    return out


# ---- values ----

def toml_value(value):
    """A value as TOML writes it: a string as a basic string (JSON's escapes are TOML's), numbers,
    booleans."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(value)
        return text if any(c in text for c in ".eE") else text + ".0"
    return json.dumps(str(value))


def checked(field, value, kind=None):
    """A field's value as llm.toml keeps it (ConfigError where it is not one)."""
    if field not in FIELDS:
        raise ConfigError("no field %s (set writes %s)" % (field, ", ".join(FIELDS)))
    want = FIELDS[field]
    if want is bool:
        if not isinstance(value, bool):
            raise ConfigError("%s is true or false" % field)
        return value
    if want in (int, float):
        try:
            number = want(value) if not isinstance(value, bool) else None
        except (TypeError, ValueError):
            number = None
        if number is None or (want is float and not math.isfinite(number)) or number < 0:
            raise ConfigError("%s is a number of 0 or more, not %r" % (field, value))
        return number
    text = str(value).strip()
    if not text or "\n" in text or "\r" in text:
        raise ConfigError("%s is one line of text" % field)
    if field == "kind" and text not in llm.KINDS:
        raise ConfigError("kind is one of %s, not %r" % (", ".join(llm.KINDS), text))
    if field == "url" and not URL.match(text):
        raise ConfigError("url is the server's base, http:// or https://, not %r" % text)
    if field == "key_env" and not ENV.match(text):
        raise ConfigError("key_env names an environment variable (letters, digits, _), not %r" % text)
    if field in ("key_env", "key_file") and kind == "command":
        raise ConfigError("a command provider takes no key: its command finds its own")
    return text


# ---- changing llm.toml ----

def read(path):
    """(text, parsed) of the configuration file; ("", {}) where there is none yet."""
    if not os.path.exists(path):
        return "", {}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        return text, tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError("%s does not parse (%s): fix it by hand first" % (path, e))


def edited(text, name, changes, unset=(), default=False):
    """The text of llm.toml with provider `name`'s fields changed ({field: value}, checked), the fields
    in unset taken out and, with default, `default` set to it: lines edited (see the module)."""
    items = statements(text)
    for s in items:
        inline = s["kind"] == "kv" and (s["table"] == ("providers",) and s["parts"][:1] == (name,) or
                                        s["table"] == () and s["parts"][:2] == ("providers", name))
        nested = s["kind"] in ("table", "array") and s["parts"][:2] == ("providers", name) and len(s["parts"]) > 2
        if inline or nested:
            raise ConfigError("provider %s is written as an inline table, with dotted keys or with tables of its "
                              "own: change it by hand" % name)
    edits = []                                  # (start, end, new text)
    header = next((k for k, s in enumerate(items) if s["kind"] == "table" and s["parts"] == ("providers", name)),
                  None)
    if header is None:
        if unset:
            raise ConfigError("no provider %s to take fields from" % name)
        block = "[providers.%s]\n" % name + "".join(
            "%s = %s\n" % (f, toml_value(changes[f])) for f in FIELDS if f in changes)
        tail = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        edits.append((len(text), len(text), tail + block))
    else:
        mine, k = [], header + 1
        while k < len(items) and items[k]["kind"] == "kv":
            if len(items[k]["parts"]) == 1:
                mine.append(items[k])
            k += 1
        fields = {s["parts"][0]: s for s in mine}
        after = mine[-1]["end"] if mine else items[header]["end"]
        added = ""
        for field in FIELDS:
            if field in changes and field in fields:
                v0, v1 = fields[field]["value"]
                edits.append((v0, v1, toml_value(changes[field])))
            elif field in changes:
                added += "%s = %s\n" % (field, toml_value(changes[field]))
        for field in unset:
            if field in fields:
                edits.append((fields[field]["start"], fields[field]["end"], ""))
        if added:
            edits.append((after, after, ("" if after == 0 or text[after - 1] == "\n" else "\n") + added))
    if default:
        root = [s for s in items if s["kind"] == "kv" and s["table"] == () and s["parts"] == ("default",)]
        if root:
            edits.append((root[0]["value"][0], root[0]["value"][1], toml_value(name)))
        else:
            keys = [s for s in items if s["kind"] == "kv" and s["table"] == ()]
            first = next((s for s in items if s["kind"] != "kv"), None)
            line = "default = %s\n" % toml_value(name)
            if keys:
                at = keys[-1]["end"]
                edits.append((at, at, ("" if text[at - 1:at] == "\n" else "\n") + line))
            elif first is not None:
                edits.append((first["start"], first["start"], line + "\n"))
            else:
                edits.append((0, 0, line + ("\n" if text else "")))
    for start, end, new in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        text = text[:start] + new + text[end:]
    return text


def set_provider(path, name, changes=None, unset=(), default=False):
    """Add provider `name` to the configuration file at path, or change its fields (changes: {field:
    value}), take out those in unset, make it the default: [(field, old value or None, new value or
    None)] of what changed. The file's other lines stay as they were (see the module)."""
    if not NAME.match(name or ""):
        raise ConfigError("a provider's name is letters, digits, - and _ (at most 64), not %r" % (name,))
    text, data = read(path)
    providers = data.get("providers", {})
    if not isinstance(providers, dict):
        raise ConfigError("%s: providers is not a table" % path)
    old = providers.get(name)
    kind = (changes or {}).get("kind") or (old or {}).get("kind")
    changes = {f: checked(f, v, kind) for f, v in (changes or {}).items()}
    unset = list(dict.fromkeys(unset or ()))
    for field in unset:
        if field not in FIELDS:
            raise ConfigError("no field %s (set writes %s)" % (field, ", ".join(FIELDS)))
        if field in REQUIRED:
            raise ConfigError("%s cannot be taken out: give another one" % field)
        if field in changes:
            raise ConfigError("%s is both set and taken out" % field)
    if old is None:
        missing = [f for f in REQUIRED if f not in changes]
        if missing:
            raise ConfigError("a new provider needs %s" % " and ".join("--" + f.replace("_", "-") for f in missing))
        if changes["kind"] == "command":
            raise ConfigError("a command provider's command line is written into llm.toml by hand (dread.llm shows "
                              "how); set changes its other fields")
    elif not isinstance(old, dict):
        raise ConfigError("provider %s is not a table: change it by hand" % name)
    if "key_file" in changes:
        why = llm.check_key_file(llm.key_path(changes, path), exists=False)
        if why:
            raise ConfigError(why)
    new_text = edited(text, name, changes, unset, default)
    try:
        new = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as e:          # (a fault of the editor: the file is left as it was)
        raise ConfigError("the edited configuration would not parse (%s); %s is unchanged" % (e, path))
    want = dict(old or {}, **changes)
    for field in unset:
        want.pop(field, None)
    others = {k: v for k, v in data.items() if k not in ("providers", "default")}
    if new.get("providers", {}).get(name) != want or \
            {k: v for k, v in new.get("providers", {}).items() if k != name} != \
            {k: v for k, v in providers.items() if k != name} or \
            {k: v for k, v in new.items() if k not in ("providers", "default")} != others or \
            new.get("default") != (name if default else data.get("default")):
        raise ConfigError("the edited configuration does not read back as asked; %s is unchanged" % path)
    write_text(path, new_text)
    done = [(f, (old or {}).get(f), changes[f]) for f in FIELDS if f in changes and (old or {}).get(f) != changes[f]]
    done += [(f, (old or {}).get(f), None) for f in unset if f in (old or {})]
    if default and data.get("default") != name:
        done.append(("default", data.get("default"), name))
    return done


def write_text(path, text):
    """Write a file whole beside it and rename it into place, its mode kept (a new file: 644 under the
    umask, its directory made)."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else None
    tmp = os.path.join(folder, ".%s.%d.tmp" % (os.path.basename(path), os.getpid()))
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ---- keys ----

def default_key_file(path, name):
    """Where a provider's key goes when nothing names a place: keys/NAME beside the configuration file
    (as llm.toml writes it: relative to its directory)."""
    return os.path.join("keys", name)


def clean_key(key):
    """A key as it is kept: without surrounding blanks; ConfigError for none, one with blanks inside or
    one longer than KEY_MAX (the message never holds it)."""
    key = (key or "").strip()
    if not key:
        raise ConfigError("no key given")
    if any(c.isspace() for c in key) or not key.isprintable():
        raise ConfigError("a key is one word of printable characters")
    if len(key) > KEY_MAX:
        raise ConfigError("a key of more than %d characters" % KEY_MAX)
    return key


def write_key(path, name, key, file=None):
    """Write a provider's key into its key file (file: as llm.toml's key_file takes it, ~ for the home and
    relative to the configuration's directory; default the key_file the provider names, else keys/NAME)
    with mode 600, and make the provider's key_file name it: (the file's absolute path, whether llm.toml
    changed). ConfigError, without the key, where anything refuses."""
    key = clean_key(key)
    text, data = read(path)
    spec = (data.get("providers") or {}).get(name)
    if not isinstance(spec, dict):
        raise ConfigError("no provider %s in %s (dread llm set %s --kind ... --model ... adds it)" % (name, path, name))
    if spec.get("kind") == "command":
        raise ConfigError("a command provider takes no key: its command finds its own")
    value = file or spec.get("key_file") or default_key_file(path, name)
    where = llm.key_path({"key_file": value}, path)
    why = llm.check_key_file(where, exists=False)
    if why:
        raise ConfigError(why)
    folder = os.path.dirname(where)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    why = llm.check_key_file(where, exists=False)          # (the directory as it is now)
    if why:
        raise ConfigError(why)
    tmp = os.path.join(folder, ".%s.%d.tmp" % (os.path.basename(where), os.getpid()))
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, (key + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, where)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    why = llm.check_key_file(where)
    if why:
        raise ConfigError("the key file written is refused: %s" % why)
    changed = False
    if llm.key_path(spec, path) != where:
        set_provider(path, name, {"key_file": value})
        changed = True
    return where, changed


def file_keys(path=None):
    """[(key, "key_file of NAME")] of the providers whose key file passes the checks: what dread gui hides in
    its jobs' logs (dread.jobs add_secret) besides the environment's secrets. Nothing for a file that does
    not parse or a key file that is refused."""
    path = path or llm.config_path()
    try:
        _, data = read(path)
    except (ConfigError, OSError):
        return []
    out = []
    for name, spec in sorted((data.get("providers") or {}).items()):
        if isinstance(spec, dict) and spec.get("key_file"):
            try:
                out.append((llm.read_key_file(llm.key_path(spec, path)), "key_file of %s" % name))
            except llm.ProviderError:
                pass
    return out


def key_words(status):
    """A provider's key in words, as dread llm providers lists it (dread.llm key_status)."""
    if status["set"]:
        return "key: " + status["how"]
    if status["problem"]:
        return "key: not set (%s)" % status["problem"]
    return "key: not set" if status["needed"] else "no key"


# ---- the commands ----

def add_parsers(sub):
    """dread llm set and dread llm key, as subcommands of dread llm's parser."""
    s = sub.add_parser("set", help="add a provider to llm.toml or change its fields (its other lines kept)")
    s.add_argument("name", help="the provider's name in llm.toml")
    s.add_argument("--kind", choices=list(llm.KINDS), help="openai: an OpenAI-compatible server; anthropic; command: a "
                   "command line that answers (written by hand)")
    s.add_argument("--model", help="the model it asks")
    s.add_argument("--url", help="the server's base URL (an OpenAI-compatible server: http://127.0.0.1:8080)")
    s.add_argument("--price-in", type=float, metavar="USD", help="US dollars per million tokens in (its price list)")
    s.add_argument("--price-out", type=float, metavar="USD", help="US dollars per million tokens out")
    s.add_argument("--cache-min", type=int, metavar="TOKENS", help="the model's least cacheable prompt (Anthropic's "
                   "prompt cache: 512 for Haiku, Sonnet and Opus 5.5)")
    s.add_argument("--batch", action=__import__("argparse").BooleanOptionalAction, default=None,
                   help="Message Batches for large rounds of questions (Anthropic; on by default)")
    s.add_argument("--key-env", metavar="VARIABLE", help="the environment variable that holds its key")
    s.add_argument("--key-file", metavar="PATH", help="the file that holds its key (dread llm key writes one)")
    s.add_argument("--default", action="store_true", help="make it the default provider")
    s.add_argument("--unset", action="append", default=[], choices=[f for f in FIELDS if f not in REQUIRED],
                   metavar="FIELD", help="take a field out (url, key_env, key_file, price_in, price_out, cache_min, "
                   "batch)")
    k = sub.add_parser("key", help="read a provider's key from standard input into its key file (mode 600)")
    k.add_argument("name", help="the provider's name in llm.toml")
    k.add_argument("--file", metavar="PATH", help="the key file (default: the provider's key_file, else keys/NAME "
                   "beside llm.toml)")


def run(args):
    path = llm.config_path()
    try:
        if args.action == "set":
            changes = {f: getattr(args, f) for f in FIELDS if getattr(args, f, None) is not None}
            if "key_file" in changes:                   # (a path given here is the shell's: from this directory)
                changes["key_file"] = os.path.abspath(os.path.expanduser(changes["key_file"]))
            if not changes and not args.unset and not args.default:
                raise ConfigError("nothing to change: give a field (dread llm set --help)")
            done = set_provider(path, args.name, changes, args.unset, args.default)
            if not done:
                print("%s: provider %s unchanged" % (path, args.name))
            for field, before, after in done:
                print("%s: provider %s: %s %s" % (path, args.name, field, "taken out" if after is None else
                                                  "%s (was %s)" % (toml_value(after), toml_value(before))
                                                  if before is not None else "%s (new)" % toml_value(after)))
            return 0
        if args.action == "key":
            if sys.stdin is not None and sys.stdin.isatty():
                import getpass
                key = getpass.getpass("the key of %s (not shown): " % args.name)
            else:
                key = sys.stdin.readline() if sys.stdin is not None else ""
            where, changed = write_key(path, args.name, key,
                                       os.path.abspath(os.path.expanduser(args.file)) if args.file else None)
            print("the key of %s is in %s (mode 600)%s" % (args.name, where,
                                                          "; llm.toml's key_file names it" if changed else ""))
            return 0
    except (ConfigError, OSError) as e:
        print("dread llm: %s" % e, file=sys.stderr)
        return 1
    raise ValueError(args.action)
