"""Reading aids: constant arguments at a call (dread.constants), dread callsites and dread flat on
gizmo's two builds."""
import pytest

from dread import callsites, constants, flat
from dread.program import Program


def test_constant_arguments(program):
    fn = program.by_name["print_status"]
    first = program.arch.arg_regs(1)[0]
    texts = []
    for callee, caller, address, kind in callsites.sites(program, ["uart_puts"]):
        if caller is fn:
            value = constants.at(program, fn, address, [first]).get(first)
            texts.append(constants.string_at(program, value) if value is not None else None)
    assert texts == ["mode ", None, " volume ", None, "\\r\\n"]     # the rest come from callees or the stack


def test_a_constant_behind_a_conditional_branch(program):
    """dispatch(3, c - '0') behind cmp r1, #9; bhi: the fill values take the branch, the second try
    starts behind it."""
    [(callee, caller, address, kind)] = callsites.sites(program, ["dispatch"])
    first, second = program.arch.arg_regs(2)
    assert constants.at(program, caller, address, [first, second]) == {first: 3}


def test_calls_grouped_by_an_argument(program, variant):
    first = program.arch.arg_regs(1)[0]
    groups = {}
    for callee, caller, address, kind in callsites.sites(program, ["gpio_set"]):
        groups.setdefault(constants.at(program, caller, address, [first]).get(first), set()).add(caller.name)
    expected = {1: {"cmd_led", "led_on", "led_off"}, 2: {"cmd_beep", "beep_on", "beep_off"}}
    if variant == "rv32":       # clang inlined the handlers into dispatch: one call, reached with 1 and with 2
        expected[None] = {"dispatch"}
    assert groups == expected


def test_call_chains(program):
    assert callsites.chains(program, "main", "uart_putc")[0] in (["main", "uart_puts", "uart_putc"],
                                                                 ["main", "handle_key", "uart_putc"])


def test_flat_listing(program):
    lines = flat.listing(program, program.by_name["print_status"], callers=flat.callers_of(program))
    text = "\n".join(lines)
    assert lines[0].startswith("; print_status") and "called by: " in text and "handle_key" in text
    assert "<uart_puts>" in text and '"mode "' in text and '" volume "' in text
    assert "<g_config>" in text                                   # a literal pointing at named data


import shutil

from conftest import gizmo_dir


@pytest.fixture
def stripped(tmp_path):
    """gizmo-m3 with every function name replaced by FUN_address."""
    copy = tmp_path / "m3"
    shutil.copytree(gizmo_dir("m3"), copy, ignore=shutil.ignore_patterns("build"))
    functions = copy / "config" / "functions.tsv"
    rows = functions.read_text().splitlines()
    functions.write_text("\n".join([rows[0]] + ["%s\tFUN_%08x\t%s" % (r.split("\t")[0], int(r.split("\t")[0], 16),
                                                                       r.split("\t")[2]) for r in rows[1:] if r])
                         + "\n")
    symbols = copy / "config" / "symbols.txt"
    symbols.write_text("\n".join("f %s FUN_%08x" % (l.split()[1], int(l.split()[1], 16))
                                 if l.split()[:1] == ["f"] else l for l in symbols.read_text().splitlines()) + "\n")
    from dread.target import Target
    target = Target.load(str(copy))
    return Program(target, target.image())


def real_names():
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    return {fn.entry: fn.name for fn in Program(target, target.image()).functions}


def test_samecode_gives_a_stripped_copy_its_names_back(stripped):
    from dread import samecode
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    found = samecode.pairs(stripped, Program(target, target.image()))
    names = real_names()
    assert len(found) >= 40 and all(names[a.entry] == b.name for a, b in found)


def test_callpair_names_the_thumb_build_from_the_arm_build(stripped):
    """Different instruction sets, the same calls: one seed (main) names most of the rest."""
    from dread import callpair
    from dread.target import Target
    target = Target.load(gizmo_dir("a32"))
    arm = Program(target, target.image())
    found, votes = callpair.propagate(stripped, arm, {stripped.by_name["FUN_08000738"].entry: arm.by_name["main"].entry})
    names = real_names()
    proposals = {a: arm.by_entry[b].name for a, b in found.items()}
    assert len(proposals) >= 15 and all(names[a] == n for a, n in proposals.items()), proposals
