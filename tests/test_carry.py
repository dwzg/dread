"""dread.naming carry: names proposed for one readable C taken over to the C the transformations make
later, each to the variable of the same identity (a parameter by its position, a local by its register
or slot and the instructions that define it), only where that is a unique match and the C with the
names still compiles to the same code; an entry from before identities applies by its digest alone."""
import json
import os
import re
import shutil

import pytest

from dread import cparse, naming

HEAD = """/* f at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 g(u32);
extern u32 h(u32);
"""

# The C the names were made for: r4 holds g's result, then h's, then a sum, one web each.
OLD = HEAD + """u32 f(u32 r0)
{
    u32 r4, r4_1, r4_2;
    r4 = g(r0);  /* 4: bl #0x100 */
    r4_1 = h(r4);  /* 8: bl #0x104 */
    r4_2 = r4_1 + r0;  /* c: adds r4, r4, r0 */
    return r4_2;  /* 10: pop {r4, pc} */
}
"""

# The C made later: g's result is no longer kept (its caller no longer passes it on), so the webs are
# numbered anew: h's result is r4 now, the sum r4_1.
NEW = HEAD + """u32 f(u32 r0)
{
    u32 r4, r4_1;
    g(r0);  /* 4: bl #0x100 */
    r4 = h(0);  /* 8: bl #0x104 */
    r4_1 = r4 + r0;  /* c: adds r4, r4, r0 */
    return r4_1;  /* 10: pop {r4, pc} */
}
"""

NAMES = {"r0": "base", "r4": "handle", "r4_1": "count", "r4_2": "total"}


def parsed(text):
    tree = cparse.parse(text)
    return tree, tree.function("f")


def entry_for(text, names, ids=True):
    """A table entry as dread pilot keeps it for the C of text (identities only with ids)."""
    _, fn = parsed(text)
    out = {"digest": naming.digest(fn), "names": dict(names), "what": "adds", "by": "test"}
    if ids:
        out["ids"] = naming.identities(fn)
    return out


def carried(text, entry):
    tree, fn = parsed(text)
    return tree, fn, naming.carry(tree, fn, entry, naming.digest(fn), naming.identities(fn))


def test_identities_are_positions_and_definitions():
    _, fn = parsed(OLD)
    assert naming.identities(fn) == {"r0": "param 0", "r4": "r4 0x4", "r4_1": "r4 0x8", "r4_2": "r4 0xc"}
    assert naming.parse_identity("r4 0x8 0xc") == ("r4", ["0x8", "0xc"]) and naming.parse_identity("param 1") == (
        "param", 1)
    assert naming.parse_identity("r4") == ("r4", []) and naming.parse_identity({"param": 0}) is None
    assert [naming.base_of(n) for n in ("r4_1", "r4_1_2", "s36_1", "r10", "cc_1")] == ["r4", "r4", "s36", "r10", "cc"]


def test_a_match_is_each_others_only_best():
    """Two old locals merged into one new one: the one that shares most of its definitions gets it,
    a tie gets nothing; a local never goes to another register's, a parameter only to its position."""
    old = {"r0": "param 0", "r1": "param 1", "r4": "r4 0x4 0x8", "r4_1": "r4 0xc", "r5": "r5 0x10", "r5_1": "r5 0x14"}
    new = {"r0": "param 0", "r4": "r4 0x4 0x8 0xc", "r5": "r5 0x10 0x14", "r6": "r6 0x10"}
    assert naming.carry_map(old, new) == {"r0": "r0", "r4": "r4"}     # (2/3 against 1/3; r5: 1/2 each)
    assert naming.carry_map(old, new, bar=0.3) == {"r0": "r0", "r4": "r4"}  # (a tie stays a tie)
    assert naming.carry_map({"r4": "r4 0x4"}, {"r6": "r6 0x4"}) == {}
    assert naming.carry_map({"r4": "r4 0x4", "r5": {"defs": ["0x8"]}}, {"r4": "r4 0x4", "r5": "r5 0x8"}) == {"r4": "r4"}


def test_a_name_follows_its_variable_when_the_webs_are_numbered_anew():
    """Applied by the variables' names the old map would put handle on h's result and count on the
    sum; carried by identity, count goes to h's result (r4 now), total to the sum (r4_1), and handle,
    whose value the new C no longer keeps, is dropped. The new entry is made for the new C, says what
    was carried and applies like any other."""
    tree, fn, new = carried(NEW, entry_for(OLD, NAMES))
    assert new["names"] == {"r0": "base", "r4": "count", "r4_1": "total"}, new
    assert new["digest"] == naming.digest(fn) and new["ids"] == naming.identities(fn)
    assert new["carried"] == {"from": entry_for(OLD, NAMES)["digest"], "names": 3, "dropped": {"r4": "handle"},
                              "open": []}
    assert naming.apply(tree, fn, new)
    text = cparse.unparse(tree)
    assert "count = h(0);" in text and "total = count + base;" in text and "return total;" in text, text
    assert "(names: test, carried from earlier C)" in text, text


def test_a_name_is_dropped_when_its_variables_definitions_changed():
    """A variable defined at another instruction now (r6), or split in two halves (r5: each half
    shares one of its two definitions, not more than CARRY_OVERLAP), loses its name; one that kept
    most of its definitions (r7: two of three) keeps it. The variables of the new C left without a name
    that the old C did not have, so identified, are open: what a new proposal would name."""
    old = HEAD + """u32 f(u32 r0)
{
    u32 r5, r6, r7;
    if (r0 == 0)  /* 4: cbnz r0, #0xc */
        r5 = 1;  /* 6: movs r5, #1 */
    else
        r5 = 2;  /* c: movs r5, #2 */
    r6 = g(r5);  /* 10: bl #0x100 */
    r7 = h(r6);  /* 14: bl #0x104 */
    if (r7 == 0) r7 = 4;  /* 18: cbnz r7, #0x1c */
    if (r7 == 1) r7 = 5;  /* 1c: cmp r7, #1 */
    return r7;  /* 20: pop {r4, pc} */
}
"""
    new = HEAD + """u32 f(u32 r0)
{
    u32 r5, r5_1, r6, r7, r8;
    if (r0 == 0)  /* 4: cbnz r0, #0xc */
    {
        r5 = 1;  /* 6: movs r5, #1 */
        r8 = g(r5);  /* 10: bl #0x100 */
    }
    else
    {
        r5_1 = 2;  /* c: movs r5, #2 */
        r8 = g(r5_1);  /* 10: bl #0x100 */
    }
    r6 = r8;  /* 12: mov r6, r8 */
    r7 = h(r6);  /* 14: bl #0x104 */
    if (r7 == 0) r7 = 4;  /* 18: cbnz r7, #0x1c */
    return r7;  /* 20: pop {r4, pc} */
}
"""
    _, fn = parsed(old)
    assert naming.identities(fn)["r7"] == "r7 0x14 0x18 0x1c"
    names = {"r0": "flag", "r5": "choice", "r6": "handle", "r7": "result"}
    _, _, got = carried(new, entry_for(old, names))
    assert got["names"] == {"r0": "flag", "r7": "result"}, got
    assert got["carried"]["dropped"] == {"r5": "choice", "r6": "handle"}
    assert got["carried"]["open"] == ["r5", "r5_1", "r6", "r8"], got["carried"]


def test_an_entry_without_identities_applies_by_its_digest_only():
    """A table from before identities: its entries apply to the C they were made for, and never
    carry; a new C waits for a new proposal, as before."""
    old_entry = entry_for(OLD, NAMES, ids=False)
    assert carried(NEW, old_entry)[2] is None
    tree, fn = parsed(NEW)
    assert not naming.apply(tree, fn, old_entry) and "handle" not in cparse.unparse(tree)
    tree, fn = parsed(OLD)
    assert naming.apply(tree, fn, old_entry) and "handle = g(base);" in cparse.unparse(tree)
    assert carried(OLD, entry_for(OLD, NAMES))[2] is None        # (made for this C: nothing to carry)


def test_a_carried_name_the_checks_refuse_is_dropped_alone():
    """A carried name that the new C uses for something else (here a callee it calls now) is left out,
    the others carried; its variable is open."""
    new = NEW.replace("g(r0);  /* 4", "handle_irq(r0);  /* 4").replace("extern u32 g(u32);",
                                                                      "extern u32 handle_irq(u32);")
    _, _, got = carried(new, entry_for(OLD, {"r0": "base", "r4_1": "handle_irq", "r4_2": "total"}))
    assert got["names"] == {"r0": "base", "r4_1": "total"}, got
    assert got["carried"]["dropped"] == {"r4_1": "handle_irq"} and got["carried"]["open"] == ["r4"]


def gizmo(tmp_path):
    """(target, image, program, context, lifter) of a fresh copy of gizmo's m3 build."""
    from conftest import GIZMO
    from dread import lift, readable, regflow
    from dread.program import Program
    from dread.target import Target
    shutil.copytree(GIZMO, tmp_path / "gizmo", ignore=shutil.ignore_patterns("build"))
    target = Target.load(str(tmp_path / "gizmo" / "m3"))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    return target, image, program, readable.Context(target, image, program, flow), lift.make_lifter(program, flow)


def made_for_other_c(made, name, names, drop=()):
    """A table entry for the function's C as if it had been made for other C (another digest), its
    identities those of this C but for the variables in drop (new in this C, then)."""
    ids = {v: i for v, i in made[name][5].items() if v not in drop}
    return {"digest": "0" * 16, "names": names, "ids": ids, "what": "does its thing", "by": "test"}


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_dread_readable_carries_names_only_where_the_code_stays_the_same(tmp_path, capsys):
    """dread readable --carry on gizmo m3: an entry made for other C goes to this C (fmt_dec: every
    name to its variable, the comment says they were carried); one whose name would make the C
    compile to other code (DREAD_H, a macro of dread.h, which no identifier check knows) stays as it
    was, waiting for a new proposal, as does one whose only name the identifier check refuses (memcpy's
    r3 named memcpy); one without identities is left alone."""
    from dread import readable
    target, image, program, context, lifter = gizmo(tmp_path)
    made = readable.question_trees(program, context, lifter, ["fmt_dec", "strlen", "fmt_hex", "memcpy"])
    good = {v: "n_" + v for v in made["fmt_dec"][5] if v not in ("frame", "sp0")}
    table = {"fmt_dec": made_for_other_c(made, "fmt_dec", good),
             "strlen": made_for_other_c(made, "strlen", {"r0": "text", "r3": "DREAD_H"}),
             "memcpy": made_for_other_c(made, "memcpy", {"r3": "memcpy"}),
             "fmt_hex": {"digest": "0" * 16, "names": {"r0": "value"}, "what": "", "by": "test"}}
    naming.store_all(image, table)
    assert readable.main(["--target", target.root, "--carry"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"fmt_dec +%d names carried, 0 dropped" % len(good), out), out
    assert re.search(r"strlen +no names carried: with them the C compiles to other code", out), out
    assert re.search(r"memcpy +no names carried: the check refused every one", out), out
    assert "of 4 functions named for other C, 1 carried (%d names, 0 dropped; 0 with variables left without a " \
           "name), 1 without identities, 2 carried nothing" % len(good) in out, out
    after = naming.load(image)
    assert after["fmt_dec"]["digest"] == made["fmt_dec"][2] and after["fmt_dec"]["names"] == good
    assert after["fmt_dec"]["carried"]["from"] == "0" * 16
    assert all(after[n] == table[n] for n in ("strlen", "memcpy", "fmt_hex"))
    text = readable.transform("fmt_dec", lifter.lift(program.by_name["fmt_dec"]).text,
                              context=readable.Context(target, image, program))
    assert "n_r1_1" in text and "(names: test, carried from earlier C)" in text, text


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_pilot_asks_again_only_where_a_carry_left_variables_without_names(tmp_path):
    """dread pilot --task readable after the C changed: memcpy's names carry whole and it is not asked;
    checksum's carry leaves r2 without a name (new in this C), so it is asked, its question showing the
    carried names in its C; the answer names r2 and keeps the rest; strlen's entry has no identities and
    is asked from scratch. The proposals the pilot keeps carry their C's identities."""
    from dread import llm, pilot
    from dread import readable
    target, image, program, context, lifter = gizmo(tmp_path)
    made = readable.question_trees(program, context, lifter, ["memcpy", "checksum", "strlen"])
    naming.store_all(image, {
        "memcpy": made_for_other_c(made, "memcpy", {"r0": "dst", "r1": "src", "r2": "len", "r4": "byte"}),
        "checksum": made_for_other_c(made, "checksum", {"r0": "data", "r1": "len", "r3": "sum"}, drop=("r2",)),
        "strlen": {"digest": "0" * 16, "names": {"r0": "text"}, "what": "", "by": "test"}})
    asked = []

    def answer(messages):
        text = messages[0]["content"]
        name = re.match(r"The function (\w+) ", text).group(1)
        asked.append((name, text))
        if name == "checksum":
            assert "u32 checksum(u32 data, u32 len)" in text and "Some of its variables have names already" in text
            assert "name the others (r2)." in text, text
            return '```json\n{"what": "sums bytes", "names": {"r2": "byte", "sum": "total"}}\n```'
        found = sorted(set(re.findall(r"\b(r\d+(?:_\d+)?)\b", text)))
        return '```json\n{"what": "counts", "names": %s}\n```' % json.dumps({v: "s_" + v for v in found})
    lines = []
    context = readable.Context(target, image, program)
    named, n_asked, _ = pilot.name_variables(target, image, program, lambda: llm.Scripted(answer),
                                             ["memcpy", "checksum", "strlen"], context, lifter, jobs=1,
                                             out=lines.append)
    assert sorted(n for n, _ in asked) == ["checksum", "strlen"] and (named, n_asked) == (3, 2), lines
    assert any(re.match(r"memcpy +4 names carried, 0 dropped$", line) for line in lines), lines
    assert any(re.match(r"memcpy +named already \(names carried\)", line) for line in lines), lines
    table = naming.load(image)
    assert table["memcpy"]["carried"]["names"] == 4 and table["memcpy"]["digest"] == made["memcpy"][2]
    assert table["checksum"]["names"] == {"r0": "data", "r1": "len", "r3": "total", "r2": "byte"}
    assert "carried" not in table["checksum"] and table["checksum"]["ids"] == made["checksum"][5]
    assert table["strlen"]["names"]["r0"] == "s_r0" and table["strlen"]["ids"] == made["strlen"][5]
    assert os.path.exists(os.path.join(target.build, "pilot", image.name, "checksum.readable.md"))


@pytest.mark.slow
@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_check_carries_names_at_its_end(tmp_path, monkeypatch):
    """dread readable --check, the step that runs after the transformations change, carries at its
    end: an entry made for other C goes to this C; one made for this C is left alone, its C not made
    again for it (the check's digests tell)."""
    from conftest import GIZMO, gizmo_dir
    from dread import lift, readable, regflow
    from dread.program import Program
    from dread.target import Target
    shutil.copytree(GIZMO, tmp_path / "gizmo", ignore=shutil.ignore_patterns("build"))
    cases = os.path.join(gizmo_dir("m3"), "build", "autocases")
    if os.path.isdir(cases):
        shutil.copytree(cases, tmp_path / "gizmo" / "m3" / "build" / "autocases")
    target = Target.load(str(tmp_path / "gizmo" / "m3"))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    made = readable.question_trees(program, readable.Context(target, image, program, flow), lifter,
                                   ["fmt_dec", "strlen"])
    now = {"digest": made["strlen"][2], "names": {"r0": "text"}, "ids": made["strlen"][5], "what": "", "by": "test"}
    names = {"r0": "buffer", "r1": "value", "r3": "length"}
    naming.store_all(image, {"fmt_dec": made_for_other_c(made, "fmt_dec", names), "strlen": now})
    asked, real = [], readable.question_trees
    monkeypatch.setattr(readable, "question_trees", lambda program, context, lifter, names, jobs=None:
                        asked.append(list(names)) or real(program, context, lifter, names, jobs))
    lines = []
    assert readable.check(target, image, ["fmt_dec", "strlen"], out=lines.append) == ["fmt_dec", "strlen"], lines
    assert asked == [["fmt_dec"]], asked
    assert any(re.match(r"fmt_dec +3 names carried, 0 dropped", line) for line in lines), lines
    assert any("of 1 function named for other C, 1 carried (3 names" in line for line in lines), lines
    table = naming.load(image)
    assert table["fmt_dec"]["digest"] == made["fmt_dec"][2] and table["fmt_dec"]["names"] == names
    assert table["strlen"] == now
