"""Every command's parser read without running the command (dread.cli parser), and its form (dread.cliforms):
the fields, the options tied to a choice of another field, and the round trip of a form's values through
the command line for every command, its subcommands and the command lines of the README."""
import argparse
import os
import shlex

import pytest

from dread import cli, cliforms


def test_every_command_yields_a_parser(tmp_path, monkeypatch, capsys):
    """Each command's main hands over its parser before it does anything: in an empty directory with an
    empty home nothing is written or printed, and argparse is as it was afterwards."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DREAD_TARGET", raising=False)
    before = argparse.ArgumentParser.parse_args, argparse.ArgumentParser.parse_known_args
    for name in cli.COMMANDS:
        parser = cli.parser(name)
        assert isinstance(parser, argparse.ArgumentParser), name
        assert parser.prog == "dread " + name, (name, parser.prog)
        form = cliforms.describe(parser)
        assert form["prog"] == parser.prog and all(f["kind"] in cliforms.FIELD_KINDS for f in form["fields"]), name
        assert cliforms.argv(parser, {}) == [] or any(f["required"] for f in form["fields"]), name
    assert os.listdir(tmp_path) == []
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
    assert (argparse.ArgumentParser.parse_args, argparse.ArgumentParser.parse_known_args) == before
    parser = argparse.ArgumentParser()                  # (parsing works as before)
    parser.add_argument("--x")
    assert parser.parse_args(["--x", "1"]).x == "1"
    with pytest.raises(KeyError):
        cli.parser("nosuch")


def test_a_main_that_does_not_parse(monkeypatch):
    import types
    module = types.ModuleType("dread_test_noparse")
    module.main = lambda argv: 0
    monkeypatch.setitem(cli.COMMANDS, "noparse", ("dread_test_noparse", "x"))
    monkeypatch.setitem(__import__("sys").modules, "dread_test_noparse", module)
    with pytest.raises(cli.NoParser, match="returned without parsing"):
        cli.parser("noparse")
    module.main = lambda argv: (_ for _ in ()).throw(SystemExit(3))
    with pytest.raises(cli.NoParser, match="SystemExit"):
        cli.parser("noparse")


# ---- the round trip ----

def accepted(action, candidates):
    """The first candidate the action's type takes."""
    for c in candidates:
        if action.type is None or isinstance(action.type, argparse.FileType):
            return c
        try:
            action.type(c)
            return c
        except (TypeError, ValueError, argparse.ArgumentTypeError):
            continue
    raise AssertionError("no sample for %s" % action)


def sample(action, n=0):
    """A value of the form for an action (n picks among choices)."""
    kind = cliforms.kind_of(action)
    if action.choices is not None and kind != "subcommands":
        choices = [str(c) for c in action.choices]
        pick = choices[n % len(choices)]
        return [pick, choices[(n + 1) % len(choices)]] if kind == "list" else pick
    if kind == "flag":
        return True
    if kind == "count":
        return 2
    if kind == "bool":
        return n % 2 == 0
    if kind == "maybe":
        return True if n % 2 else accepted(action, ["2026-10", "7", "0x10"])
    if kind in ("list", "raw"):
        one = accepted(action, ["a b", "7", "0x10"])
        two = accepted(action, ["c", "8", "0x11"])
        return [one, two] if n % 2 == 0 else shlex.join([one, two])
    return accepted(action, ["x y", "7", "0.5", "0x10"])


def value_sets(parser):
    """Form values for a parser: only what is required, everything (one of each mutually exclusive
    group), and each choice of each field with choices; the subcommands each with their own sets."""
    acts = cliforms.actions(parser)
    in_group = {}
    for n, group in enumerate(parser._mutually_exclusive_groups):
        for a in group._group_actions:
            in_group[id(a)] = n
    subs = [a for a in acts if isinstance(a, argparse._SubParsersAction)]
    plain = [a for a in acts if not isinstance(a, argparse._SubParsersAction)]

    def base(full, n=0):
        values, used = {}, set()
        for a in plain:
            if not (a.required or full):
                continue
            g = in_group.get(id(a))
            if g is not None and g in used:
                continue
            if g is not None:
                used.add(g)
            values[cliforms.field_id(a)] = sample(a, n)
        return values
    sets = [base(False), base(True), base(True, 1)]
    for a in plain:
        if a.choices is not None:
            for n in range(len(a.choices)):
                sets.append(dict(base(False), **{cliforms.field_id(a): sample(a, n)}))
    if subs:
        a = subs[0]
        out = []
        for name, sub in a.choices.items():
            for values in value_sets(sub):
                for s in sets[:2]:
                    out.append(dict(s, **{cliforms.field_id(a): {"command": name, "values": values}}))
        return out
    return sets


def round_trip(parser_of, values):
    args = cliforms.argv(parser_of(), values)
    expected = cliforms.check(parser_of(), args)
    back = cliforms.values(parser_of(), args)
    again = cliforms.argv(parser_of(), back)
    assert cliforms.check(parser_of(), again) == expected, (values, args, back, again)
    assert cliforms.argv(parser_of(), cliforms.values(parser_of(), again)) == again, (args, again)
    return args


@pytest.mark.parametrize("name", list(cli.COMMANDS))
def test_every_form_round_trips(name):
    """For every command: the command line of a form's values parses (check), the values read back from
    it make a command line the parser reads the same, and a second round trip changes nothing."""
    sets = value_sets(cli.parser(name))
    assert sets
    for values in sets:
        round_trip(lambda: cli.parser(name), values)


README_LINES = [
    "info", "lift --check", "lift --target examples/gizmo/m3 --check print_status uart_putc",
    "test --target examples/gizmo/rv32", "bench all freertos", "bench toolchain ubuntu-24.04 ubuntu-22.04 --rebuild",
    "bench report freertos --steps lift,readable", "cclab run --adapter gcc-arm", "cclab compare ti-arm gcc-arm",
    "cclab run --adapter x --build a --build 'b c' --guard", "whatcc --target ~/dread-bench/freertos/mps2-gcc7-Os",
    "flat --target examples/gizmo/m3 print_status", "callsites --target examples/gizmo/m3 --by 0 gpio_set",
    "callpair --target OTHER --other examples/gizmo/a32 --seed FUN_08000738=main --seed a=b",
    "rename --target examples/gizmo/m3 beep_on=buzzer_on", "pilot --target examples/gizmo/m3 --limit 4",
    "pilot --task name --samples 5 --no-subsystems --temperature -0.5", "pilot --usage", "pilot --usage 2026-10",
    "pilot --task name --score truth.elf --grounded p --against a.tsv b.tsv --vs c.tsv fn1 fn2",
    "llm providers", "llm ask --provider local hello world", "liftfuzz --isa t32 --isa a32",
    "readable --target examples/gizmo/m3 --out rc", "ghidra --region 0x8000", "flat -- -odd",
]


@pytest.mark.parametrize("line", README_LINES)
def test_command_lines_round_trip(line):
    """A command line as a person writes it: read into the form and written back, the parser reads it the
    same."""
    words = shlex.split(line)
    name, args = words[0], words[1:]
    expected = cliforms.check(cli.parser(name), args)
    values = cliforms.values(cli.parser(name), args)
    again = cliforms.argv(cli.parser(name), values)
    assert cliforms.check(cli.parser(name), again) == expected, (line, values, again)


def test_the_order_of_a_command_line():
    """Options of one value, positionals, then options of several values (which would take the
    positionals); -- where a positional starts with -; --option=VALUE for a value that does; a negative
    number as it is; a subcommand last."""
    def make(name, values):
        return cliforms.argv(cli.parser(name), values)
    assert make("lift", {"--check": True, "names": "a b"}) == ["--check", "a", "b"]
    args = make("pilot", {"--task": "name", "names": ["f1"], "--against": "x.tsv y.tsv", "--score": "t.elf"})
    assert args == ["--score", "t.elf", "--task", "name", "f1", "--against", "x.tsv", "y.tsv"], args
    assert cliforms.check(cli.parser("pilot"), args).names == ["f1"]
    assert make("flat", {"names": ["-odd", "even"]}) == ["--", "-odd", "even"]
    assert make("bench", {"action": "report", "recipe": "r", "--steps": "-x"}) == ["--steps=-x", "report", "r"]
    assert make("pilot", {"--temperature": "-0.5"}) == ["--temperature", "-0.5"]
    assert make("cclab", {"action": {"command": "run", "values": {"--adapter": "gcc", "--build": "a b"}}}) == \
        ["run", "--adapter", "gcc", "--build", "a", "--build", "b"]
    assert make("pilot", {"--subsystems": False}) == ["--no-subsystems"]
    assert make("pilot", {"--subsystems": None}) == []
    assert make("pilot", {"--usage": True}) == ["--usage"]
    with pytest.raises(cliforms.FormError, match="read as an option"):
        make("pilot", {"--against": ["-h"]})
    with pytest.raises(cliforms.FormError, match="unbalanced"):
        make("lift", {"names": "'a"})


def test_what_the_command_refuses():
    """check() reports argparse's message and the field it names instead of exiting; -h among the
    words is refused, not answered with the help."""
    with pytest.raises(cliforms.FormError) as e:
        cliforms.check(cli.parser("pilot"), ["--tries", "x"])
    assert "invalid int value" in str(e.value) and e.value.field == "--tries"
    with pytest.raises(cliforms.FormError, match="required"):
        cliforms.check(cli.parser("cclab"), ["doctor"])
    with pytest.raises(cliforms.FormError, match="invalid choice"):
        cliforms.check(cli.parser("pilot"), ["--task", "nothing"])
    with pytest.raises(cliforms.FormError, match="help"):
        cliforms.check(cli.parser("lift"), ["-h"])


def test_the_form_of_a_command():
    """describe(): kinds, defaults with %(default)s filled, choices, the subcommands' forms, and the
    options a help text ties to a choice of another field (pilot's tasks, bench's toolchain)."""
    form = cliforms.describe(cli.parser("pilot"))
    by = {f["id"]: f for f in form["fields"]}
    assert by["--target"]["kind"] == "value" and by["--target"]["metavar"] == "DIR"
    assert by["--no-lifted"]["kind"] == "flag" and by["names"]["kind"] == "list" and by["names"]["positional"]
    assert by["--task"]["choices"][0] == "write" and by["--task"]["default"] == "write"
    assert by["--subsystems"]["kind"] == "bool" and by["--subsystems"]["flags"] == ["--subsystems", "--no-subsystems"]
    assert by["--usage"]["kind"] == "maybe" and by["--source"]["repeat"]
    assert by["--samples"]["when"] == {"field": "--task", "in": ["name"]}
    assert by["--decider"]["when"] == {"field": "--task", "in": ["review"]}
    assert by["--jobs"]["when"] == {"field": "--task", "in": ["name", "readable", "globals", "subsystems"]}
    assert by["--gate"]["when"] == {"field": "--task", "in": ["name"]}
    assert by["--judge"]["when"] == by["--grounded"]["when"] == by["--source"]["when"] == {"field": "--task", "in": ["name"]}
    assert by["--provider"]["when"] is None and by["--tries"]["when"] is None and by["--usage"]["when"] is None
    assert "%(" not in "".join(f["help"] for f in form["fields"])
    form = cliforms.describe(cli.parser("bench"))
    by = {f["id"]: f for f in form["fields"]}
    assert by["action"]["positional"] and by["action"]["required"] and "toolchain" in by["action"]["choices"]
    assert by["--rebuild"]["when"] == {"field": "action", "in": ["toolchain"]}
    assert by["--prune"]["when"] == {"field": "action", "in": ["toolchain"]} and by["--jobs"]["when"] is None
    form = cliforms.describe(cli.parser("llm"))
    sub = form["fields"][0]
    names = [s["name"] for s in sub["subcommands"]]
    assert sub["kind"] == "subcommands" and names[:2] == ["providers", "ask"]
    ask = {f["id"]: f for f in sub["subcommands"][1]["form"]["fields"]}
    assert ask["text"]["kind"] == "list" and ask["text"]["required"] and ask["--system"]["default"]
    form = cliforms.describe(cli.parser("trace"))
    assert form["exclusive"] and len(form["exclusive"][0]) == 3
