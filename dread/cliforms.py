"""A command's argparse parser as a form, and a form's values as the command line: dread gui's Run page.

The parser is the one the command's main builds (dread.cli parser), so that the form is never another
description of the command than its --help. Four functions:
- describe(parser): the form, JSON for the page: {"prog", "description", "fields", "exclusive"}; a
  field each argument (FIELD_KINDS), with its flags, help (as --help expands it), default, choices,
  type, whether it is required, and "when" where its help text ties it to a choice of another field;
  a parser with subcommands has a "subcommands" field holding a form for each;
- argv(parser, values): the arguments after `dread COMMAND` of a form's values (keyed by field id);
- values(parser, args): the form's values of a command line, read through the parser itself;
- check(parser, args): the command line parsed as the command parses it, a FormError (argparse's own
  message) instead of an exit.
The page shows the command line argv makes and runs it; values fills the form from a command line (a
job run again with changes). For every command the round trip holds: values then argv give a command
line the parser reads as it reads the first (tests/test_cliforms.py).

Values: a field's id is its longest option string, or the dest of a positional. flag: true; count: a
number; value: text ("" or missing: not given); maybe (an option whose value may be left out, nargs
"?"): true for the option alone, else its text; list: a list of texts, or one text of shell words
(shlex: "a 'b c'" is two values); bool (--x/--no-x): true, false or null for the default;
subcommands: {"command": NAME, "values": {...}}. Texts are not converted: the parser's types decide,
and check() reports a value it refuses as the command would.

The order of a command line: options of one value first, then the positionals, then options of
several values (else they would take the positionals for theirs), "--" before positionals where one
starts with "-", a value starting with "-" as --option=VALUE; a subcommand last, with its own options
after its name. Help texts may tie an option to a choice of another field ("--task name: ...",
"toolchain: ...") or to another option ("with --score: ...", which then shares that option's tie):
describe() turns that convention into "when" ({"field": id, "in": [choices]}), and the page hides the
option unless the field has one of those choices (a switch shows them all). It reads the convention,
so it is a presentation aid: an option whose help does not follow it is always shown.

This relies on argparse's attributes of actions and parsers (_actions, option_strings, nargs, const,
choices, the subparsers' choices), stable from Python 3.2 to 3.14. A `type` that is an
argparse.FileType is not called by check() (it would open the file in the caller's process).
"""
import argparse
import functools
import re
import shlex

FIELD_KINDS = ("flag", "count", "value", "maybe", "list", "bool", "subcommands", "raw")
CONNECTIVES = {"and", "or", "with", "the", "a"}


class FormError(ValueError):
    """A form's values or a command line the command would refuse; field: the id it names, if any."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


# ---- reading a parser ----

def actions(parser):
    """The parser's actions a form shows: all but the help and version actions (and those without a
    dest, except a subcommands action, whose dest may be left out)."""
    out = []
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            out.append(a)
        elif not isinstance(a, (argparse._HelpAction, argparse._VersionAction)) and a.dest != argparse.SUPPRESS:
            out.append(a)
    return out


def field_id(action):
    """The key of an argument's value: its longest option string (--target, not -t; --x, not --no-x of
    a --x/--no-x pair), or the dest of a positional."""
    if action.option_strings:
        names = action.option_strings
        if isinstance(action, argparse.BooleanOptionalAction):
            names = [s for s in names if not s.startswith("--no-")] or names
        return max(names, key=lambda s: (s.startswith("--"), len(s)))
    return action.dest


def kind_of(action):
    if isinstance(action, argparse._SubParsersAction):
        return "subcommands"
    if isinstance(action, argparse.BooleanOptionalAction):
        return "bool"
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction, argparse._StoreConstAction,
                           argparse._AppendConstAction)):
        return "flag"
    if isinstance(action, argparse._CountAction):
        return "count"
    if isinstance(action, argparse._AppendAction):
        return "list" if action.nargs is None else "raw"
    if isinstance(action, (argparse._StoreAction, argparse._ExtendAction)):
        if action.nargs is None:
            return "value"
        if action.nargs == argparse.OPTIONAL:
            return "maybe" if action.option_strings else "value"
        return "list"
    return "raw"


def type_name(action):
    t = action.type
    if t is int:
        return "int"
    if t is float:
        return "float"
    return "text"


def expand_help(action, prog):
    """The help text as --help shows it (%(default)s and the like filled in)."""
    text = action.help or ""
    if text == argparse.SUPPRESS:
        return None
    if "%" not in text:
        return text
    params = {k: v for k, v in vars(action).items() if v is not argparse.SUPPRESS}
    params["prog"] = prog
    for k, v in list(params.items()):
        if hasattr(v, "__name__"):
            params[k] = v.__name__
    if params.get("choices") is not None:
        params["choices"] = ", ".join(str(c) for c in params["choices"])
    try:
        return text % params
    except (KeyError, ValueError, TypeError):
        return text


def default_text(action):
    """What argparse uses when the argument is not given, as text; None when that is nothing."""
    d = action.default
    if d is None or d is argparse.SUPPRESS or isinstance(action, argparse._SubParsersAction):
        return None
    if kind_of(action) == "flag":
        return None
    if isinstance(d, bool):
        return "on" if d else "off"
    if isinstance(d, (list, tuple)):
        return " ".join(str(x) for x in d) or None
    return str(d)


def describe(parser):
    """The form of a parser (see the module's docstring)."""
    fields = []
    groups = {}
    for group in parser._action_groups:
        for a in group._group_actions:
            groups[id(a)] = group.title
    exclusive = []
    for n, group in enumerate(parser._mutually_exclusive_groups):
        exclusive.append([field_id(a) for a in group._group_actions])
    member = {id(a): n for n, group in enumerate(parser._mutually_exclusive_groups) for a in group._group_actions}
    for a in actions(parser):
        kind = kind_of(a)
        f = {"id": field_id(a), "flags": list(a.option_strings), "dest": a.dest, "kind": kind,
             "positional": not a.option_strings, "required": bool(a.required), "help": expand_help(a, parser.prog),
             "metavar": a.metavar if isinstance(a.metavar, str) else None,
             "choices": [str(c) for c in a.choices] if a.choices is not None and kind != "subcommands" else None,
             "default": default_text(a), "type": type_name(a),
             "nargs": a.nargs if kind not in ("flag", "subcommands") else None,
             "repeat": isinstance(a, argparse._AppendAction), "group": None, "when": None,
             "exclusive": member.get(id(a))}
        title = groups.get(id(a))
        if title not in (None, "positional arguments", "options", "optional arguments"):
            f["group"] = title
        if f["help"] is None:
            f["hidden"] = True
            f["help"] = ""
        if kind == "bool":
            f["flags"] = [s for s in a.option_strings if not s.startswith("--no-")] + \
                         [s for s in a.option_strings if s.startswith("--no-")]
        if kind == "subcommands":
            helps = {c.dest: c.help for c in a._choices_actions}
            f["subcommands"] = [{"name": name, "help": helps.get(name) or "", "form": describe(sub)}
                                for name, sub in a.choices.items()]
            f["required"] = bool(a.required)
        fields.append(f)
    for f in fields:
        f["when"] = condition(f["help"], fields) if not f["positional"] else None
    more = True
    while more:                     # ("with --score: ...", where --score is for --task name: so is it)
        more = False
        for f in fields:
            other = with_field(f["help"], fields) if f["when"] is None and not f["positional"] else None
            if other is not None and other["when"] is not None:
                f["when"] = dict(other["when"])
                more = True
    return {"prog": parser.prog, "description": parser.description or "", "fields": fields, "exclusive": exclusive}


def with_field(text, fields):
    """The field a help text says an option goes with ("with --score: ..."), or None."""
    if not text or ":" not in text:
        return None
    words = [w.strip(",;()") for w in text.split(":", 1)[0].split()]
    if len(words) < 2 or words[0] != "with":
        return None
    return next((f for f in fields if words[1] in f["flags"]), None)


def condition(text, fields):
    """{"field": id, "in": [choices]} where a help text begins with the choices of another field it
    applies to ("--task name, readable and globals: ...", "toolchain: ..." for a positional's choice);
    None otherwise."""
    if not text or ":" not in text:
        return None
    head = text.split(":", 1)[0]
    if len(head) > 80 or ". " in head:
        return None
    words = [w.strip(",;()") for w in head.split()]
    words = [w for w in words if w]
    by_flag = {flag: f for f in fields if f["choices"] for flag in f["flags"]}
    if words and words[0] in by_flag:
        f, chosen = by_flag[words[0]], []
        for w in words[1:]:
            if w == "with" or (w.startswith("--") and w not in by_flag):
                break
            if w in f["choices"]:
                chosen.append(w)
            elif w not in CONNECTIVES:
                return None
        return {"field": f["id"], "in": chosen} if chosen else None
    for f in fields:
        if f["positional"] and f["choices"]:
            chosen = [w for w in words if w not in CONNECTIVES]
            if chosen and all(w in f["choices"] for w in chosen):
                return {"field": f["id"], "in": chosen}
    return None


# ---- a form's values as a command line ----

def words(value):
    """A list field's values: a list as it is, a text as shell words."""
    if value is None or value is False:
        return []
    if isinstance(value, str):
        try:
            return shlex.split(value)
        except ValueError as e:
            raise FormError("unbalanced quotes in %r: %s" % (value, e))
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def text(value):
    if value is None or value is False:
        return ""
    if value is True:
        return ""
    return str(value)


def truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes")
    return bool(value)


def looks_negative(parser, word):
    """A negative number argparse takes for a value (where no option looks like one)."""
    matcher = getattr(parser, "_negative_number_matcher", None) or re.compile(r"^-\d+$|^-\d*\.\d+$")
    return bool(matcher.match(word)) and not getattr(parser, "_has_negative_number_optionals", None)


def dashed(parser, word):
    """Whether a word would be taken for an option ("-" alone is a value)."""
    return len(word) > 1 and word[0] in parser.prefix_chars and not looks_negative(parser, word)


def option_words(parser, flag, value):
    """An option with one value: --flag VALUE, or --flag=VALUE (-fVALUE) where the value would be
    taken for an option."""
    if dashed(parser, value) or value == "":
        return [flag + "=" + value] if flag.startswith(parser.prefix_chars[0] * 2) else [flag + value]
    return [flag, value]


def argv(parser, values):
    """The arguments after `dread COMMAND` of a form's values (see the module's docstring)."""
    values = values or {}
    first, late, positional, sub = [], [], [], []
    for a in actions(parser):
        fid, kind = field_id(a), kind_of(a)
        v = values.get(fid)
        flag = fid if a.option_strings else None
        if kind == "subcommands":
            if not isinstance(v, dict) or not v.get("command"):
                continue
            name = str(v["command"])
            if name not in a.choices:
                raise FormError("%s has no subcommand %s" % (parser.prog, name), fid)
            sub = [name] + argv(a.choices[name], v.get("values") or {})
            continue
        if not a.option_strings:
            got = words(v) if kind in ("list", "raw") else ([text(v)] if text(v) != "" else [])
            positional += got
            continue
        if kind == "flag":
            if truthy(v):
                first.append(flag)
        elif kind == "count":
            try:
                n = int(v or 0)
            except (TypeError, ValueError):
                raise FormError("%s: a number of times, not %r" % (flag, v), fid)
            first.extend([flag] * max(0, n))
        elif kind == "bool":
            if v is None or v == "":
                continue
            on = [s for s in a.option_strings if not s.startswith("--no-")]
            off = [s for s in a.option_strings if s.startswith("--no-")]
            first.append((on or a.option_strings)[0] if truthy(v) else (off or a.option_strings)[-1])
        elif kind == "value":
            if text(v) != "":
                first.extend(option_words(parser, flag, text(v)))
        elif kind == "maybe":
            if v is True or (isinstance(v, str) and v.strip().lower() == "true"):
                late.append(flag)
            elif text(v) != "":
                late.extend(option_words(parser, flag, text(v)))
        elif kind == "list" and isinstance(a, argparse._AppendAction):
            for w in words(v):
                first.extend(option_words(parser, flag, w))
        else:                                       # (nargs +, *, N, an extend, or one argparse has no form for)
            got = words(v)
            if got:
                bad = [w for w in got if dashed(parser, w)]
                if bad:
                    raise FormError("%s: %s would be read as an option; a command line cannot give it" % (flag, bad[0]),
                                    fid)
                late.extend([flag] + got)
    if sub:
        if any(dashed(parser, w) for w in positional):
            raise FormError("%s: a positional value that starts with - before a subcommand" % parser.prog)
        return first + late + positional + sub
    if any(dashed(parser, w) for w in positional):
        return first + late + ["--"] + positional
    return first + positional + late


# ---- a command line as the command reads it ----

def parsers(parser):
    """The parser and those of its subcommands, all levels."""
    out = [parser]
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for sub in a.choices.values():
                out += parsers(sub)
    return out


def quiet(parser):
    """The parser and its subcommands' made to raise FormError where argparse would print and exit
    (an error, -h among the words). Only for parsers of one's own (dread.cli parser makes a new one
    each call): it changes them."""
    for p in parsers(parser):
        p.error = functools.partial(_error, p)
        p.exit = _leave
        p.print_help = p.print_usage = _nothing
    return parser


def _error(parser, message):
    """FormError with argparse's message, and the field it names ("argument --tries: ...")."""
    m = re.match(r"argument ([^:]+): ", message)
    field = None
    if m:
        names = m.group(1).split("/")
        field = next((field_id(a) for a in parser._actions
                      if set(names) & set(a.option_strings) or a.dest in names or a.metavar in names), None)
    raise FormError(message, field)


def _leave(status=0, message=None):
    raise FormError(message.strip() if message else "the command line asks for the command's help (-h)")


def _nothing(file=None):
    return None


def check(parser, args, namespace=None):
    """The command line parsed as the command parses it (argparse.Namespace); FormError with
    argparse's message where it would refuse it."""
    quiet(parser)
    saved = []
    for p in parsers(parser):
        for a in p._actions:
            if isinstance(a.type, argparse.FileType):
                saved.append((a, a.type))
                a.type = None
    try:
        return parser.parse_args(list(args), namespace)
    finally:
        for a, t in saved:
            a.type = t


_UNSET = object()


def values(parser, args):
    """The form's values of a command line (keyed by field id; only what it gives), read through the
    parser: each option's value as the parser stores it, written back as text. FormError where the
    parser refuses the command line. Like check(), for a parser of one's own: it changes it while it
    reads (a subcommands action's class, to see the words its subcommand got)."""
    namespace = argparse.Namespace()
    lists = (argparse._AppendAction, argparse._AppendConstAction, argparse._CountAction, argparse._ExtendAction)
    for a in actions(parser):
        if not isinstance(a, argparse._SubParsersAction):
            setattr(namespace, a.dest, None if isinstance(a, lists) else _UNSET)
    seen = {}
    spies = []
    for a in actions(parser):
        if isinstance(a, argparse._SubParsersAction):
            spies.append((a, a.__class__))
            a.__class__ = _spy(a.__class__, seen)
    try:
        parsed = check(parser, args, namespace)
    finally:
        for a, cls in spies:
            a.__class__ = cls
    out = {}
    for a in actions(parser):
        fid, kind = field_id(a), kind_of(a)
        v = getattr(parsed, a.dest, _UNSET) if a.dest not in (None, argparse.SUPPRESS) else _UNSET
        if kind == "subcommands":
            got = seen.get(id(a))
            if got:
                name, rest = got[0], got[1:]
                out[fid] = {"command": name, "values": values(a.choices[name], rest)}
            continue
        if v is _UNSET:
            continue
        if kind == "flag":
            if isinstance(a, argparse._StoreTrueAction):
                given = v is True
            elif isinstance(a, argparse._StoreFalseAction):
                given = v is False
            elif isinstance(a, argparse._AppendConstAction):
                given = isinstance(v, list) and a.const in v
            else:
                given = v == a.const and v is not None
            if given:
                out[fid] = True
        elif kind == "count":
            if v:
                out[fid] = int(v)
        elif kind == "bool":
            if isinstance(v, bool):
                out[fid] = v
        elif kind == "maybe":
            if v is a.const or (a.const is not None and v == a.const):
                out[fid] = True
            elif v is not None:
                out[fid] = str(v)
        elif kind == "value":
            if v is not None:
                out[fid] = str(v[0] if isinstance(v, list) and len(v) == 1 else v)
        else:
            if v is not None:
                items = v if isinstance(v, (list, tuple)) else [v]
                flat = []
                for x in items:
                    flat += [str(y) for y in x] if isinstance(x, (list, tuple)) else [str(x)]
                if flat:
                    out[fid] = flat
    return out


def _spy(cls, seen):
    """A subclass of a subparsers action's class that notes the words it is handed (its name and the
    words after it), so that values() reads the subcommand's own values with its own parser."""
    class Spy(cls):
        def __call__(self, parser, namespace, values, option_string=None):
            seen[id(self)] = list(values)
            return super().__call__(parser, namespace, values, option_string)
    return Spy


def line(command, args):
    """The command line as a person types it."""
    return shlex.join(["dread", command] + list(args))
