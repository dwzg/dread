"""dread.structure: gotos as if/else and loops. The shapes one by one (a while, a do-while, an
if/else, two conditions to one target as || and &&, a switch, joins in a loop that never ends, a short
way repeated, shared blocks after an if without a join, an else-if chain, early exits, a staircase of
checks, an else if printed with comments, a jump to the start of an arm as part of the condition), and
property tests: random functions of labels, arithmetic and gotos (irreducible flow among them; with
switches; flow that mostly never returns; runs of checks as compilers lay them out; checks threaded
into a shared arm), structured, compiled with the host's C compiler next to the originals and called
with random arguments: every one gives the original's result."""
import ctypes
import itertools
import random
import re
import shutil
import subprocess

import pytest

from dread import cparse, structure


def fn_of(body, extra_decls=""):
    text = ("typedef unsigned int u32;\nu32 f(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n"
            "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3;\n%s%s\n}\n" % (extra_decls, body))
    return cparse.parse(text).function("f")


def structured(body):
    fn = fn_of(body)
    structure.structure(fn)
    return cparse.unparse(fn, notes=False, style="readable")


def test_the_shapes():
    w = structured("L_1: ;\n    if (r0 == 0) goto L_2;\n    r0 = r0 - 1; r1 = r1 + 2;\n    goto L_1;\nL_2: ;\n    return r1;")
    assert "while (r0 != 0)" in w and "goto" not in w, w
    d = structured("L_1: ;\n    r0 = r0 - 1;\n    if (r0 != 0) goto L_1;\n    return r0;")
    assert "do" in d and "while (r0 != 0);" in d and "goto" not in d, d
    e = structured("    if (r0 == 0) goto L_a;\n    r1 = 1;\n    goto L_b;\nL_a: ;\n    r1 = 2;\nL_b: ;\n    return r1;")
    assert "else" in e and "goto" not in e, e
    o = structured("    if (r0 == 1) goto L_t;\n    if (r1 == 2) goto L_t;\n    return 0;\nL_t: ;\n    return 1;")
    assert "if (r0 != 1 && r1 != 2) return 0;" in o and "goto" not in o, o     # (the way on first)
    u = structured("    if (r0 == 1) goto L_t;\n    if (r1 != 2) goto L_u;\nL_t: ;\n    r2 = 5;\n    goto L_j;\n"
                   "L_u: ;\n    r2 = 7;\nL_j: ;\n    return r2 + r3;")
    assert "if (r0 == 1 || r1 == 2)" in u and u.index("r2 = 5") < u.index("r2 = 7") and "goto" not in u, u
    a = structured("    if (r0 == 1) goto L_z;\n    if (r1 != 2) goto L_e;\nL_z: ;\n    if (r2 != 3) goto L_e;\n"
                   "    r3 = 5;\n    goto L_j;\nL_e: ;\n    r3 = 7;\nL_j: ;\n    return r3 + r2;")
    assert "if ((r0 == 1 || r1 == 2) && r2 == 3)" in a and "goto" not in a, a


def random_function(rng, name, blocks=7, endless=False):
    """A function of labelled blocks: random register arithmetic, then a goto, a conditional goto or a
    return; fuel counts the labels passed, so that every run ends. endless: the fuel runs out into
    bail() (which leaves by longjmp: a plain call to the flow graph), most returns are gotos or loops
    that never end but bail out at once, and the end jumps back, so that much of the code never
    reaches a return (compiled with BAIL)."""
    regs = ["r0", "r1", "r2", "r3"]
    lines = ["u32 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{",
             "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, fuel = 40;"]
    for k in range(blocks):
        lines.append("L_%d: ;" % k)
        lines.append("    fuel = fuel - 1;")
        lines.append(("    if (fuel == 0) bail(r0 ^ %d);" if endless else "    if (fuel == 0) return r0 ^ %d;") % k)
        for _ in range(rng.randint(0, 2)):
            a, b = rng.choice(regs), rng.choice(regs)
            op = rng.choice(["+", "-", "^", "&", "|"])
            lines.append("    %s = %s %s %s;" % (a, b, op, rng.choice([b, str(rng.randint(1, 9)) + "u"])))
        kind = rng.random()
        target = rng.randrange(blocks)
        if kind < 0.45:
            a, b = rng.choice(regs), rng.choice(regs)
            lines.append("    if (%s %s %s) goto L_%d;" % (a, rng.choice(["<", "==", "!=", ">="]), b, target))
        elif kind < 0.7:
            lines.append("    goto L_%d;" % target)
        elif kind < 0.85 and k + 1 < blocks:
            pass                            # (falls through)
        elif endless and rng.random() < 0.8:
            if rng.random() < 0.25:
                lines.append("    for (;;) bail(r2 ^ %du);" % k)     # (a statement that never ends)
            else:
                lines.append("    goto L_%d;" % target)
        else:
            lines.append("    return %s;" % rng.choice(regs))
    lines.append("    goto L_0;" if endless else "    return r1;")
    lines.append("}")
    return "\n".join(lines) + "\n"


# What endless functions call when their fuel runs out: it leaves them by longjmp, to call_NAME, which
# returns the value marked (a run that bailed out never equals one that returned).
BAIL = ("#include <setjmp.h>\ntypedef unsigned int u32;\nstatic jmp_buf bail_env;\nstatic u32 bail_value;\n"
        "static void bail(u32 v)\n{\n    bail_value = v;\n    longjmp(bail_env, 1);\n}\n")


def bail_caller(name):
    return ("u32 call_%s(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n    if (setjmp(bail_env)) return bail_value ^ "
            "0x80000000u;\n    return %s(a0, a1, a2, a3);\n}\n" % (name, name))


@pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")
def test_random_flow_structured_computes_the_same(tmp_path):
    rng = random.Random(5)
    originals, results, shapes = [], [], {"goto": 0, "loops": 0}
    count = 400
    for i in range(count):
        text = random_function(rng, "orig_%d" % i, blocks=rng.randint(5, 10))
        originals.append(text)
        tree = cparse.parse("typedef unsigned int u32;\n" + text.replace("orig_", "rest_"))
        fn = tree.function("rest_%d" % i)
        structure.structure(fn)
        out = cparse.unparse(fn, notes=False, style="readable")
        shapes["goto"] += "goto" in out
        shapes["loops"] += ("for (" in out) or ("while (" in out)
        results.append(out)
    source = tmp_path / "flows.c"
    source.write_text("typedef unsigned int u32;\n" + "".join(originals) + "\n".join(results) + "\n")
    lib = tmp_path / "flows.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(source)], check=True)
    so = ctypes.CDLL(str(lib))
    for i in range(count):
        a, b = getattr(so, "orig_%d" % i), getattr(so, "rest_%d" % i)
        for f in (a, b):
            f.restype, f.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 4
        for _ in range(25):
            args = [rng.choice([0, 1, 2, 7, rng.getrandbits(32)]) for _ in range(4)]
            assert a(*args) == b(*args), (i, args, originals[i], results[i])
    assert shapes["loops"] > count // 5 and shapes["goto"] < count, shapes     # (many structured, some not)


def test_a_jump_table_switch_gets_its_cases_bodies():
    """The lifters' switch (case k: goto L; default: return r0;): each case its statements, break where
    the ways meet, cases sharing a target sharing their statements, no goto left."""
    s = structured("    switch (r0)\n    {\n    case 0: goto L_a;\n    case 1: goto L_b;\n    case 2: goto L_a;\n"
                   "    case 3: goto L_c;\n    default: return r0;\n    }\nL_a: ;\n    r1 = 5;\n    goto L_d;\n"
                   "L_b: ;\n    r1 = 7;\n    goto L_d;\nL_c: ;\n    r1 = 9;\nL_d: ;\n    return r1 + r2;")
    assert "goto" not in s and "case 2:" in s and "break;" in s and "default:" in s, s
    assert s.index("case 0:") < s.index("r1 = 5") < s.index("case 1:") < s.index("r1 = 7"), s
    assert s.count("return r1 + r2;") == 1, s
    inside = structured("L_top: ;\n    r0 = r0 - 1;\n    if (r0 == 0) goto L_out;\n    switch (r1)\n    {\n"
                        "    case 0: goto L_top;\n    case 1: goto L_out;\n    default: goto L_x;\n    }\n"
                        "L_x: ;\n    r2 = r2 + 1;\n    goto L_top;\nL_out: ;\n    return r2;")
    assert "continue;" in inside and "goto" not in inside or "goto L_out" in inside, inside


def random_switch_function(rng, name, blocks=8, endless=False):
    """As random_function, with switches among the ways out of a block: a jump table on a register's
    low bits, some cases sharing a target, a default that jumps or returns or none at all."""
    regs = ["r0", "r1", "r2", "r3"]
    lines = ["u32 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{",
             "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, fuel = 40;"]
    for k in range(blocks):
        lines.append("L_%d: ;" % k)
        lines.append("    fuel = fuel - 1;")
        lines.append(("    if (fuel == 0) bail(r0 ^ %d);" if endless else "    if (fuel == 0) return r0 ^ %d;") % k)
        for _ in range(rng.randint(0, 2)):
            a, b = rng.choice(regs), rng.choice(regs)
            op = rng.choice(["+", "-", "^", "&", "|"])
            lines.append("    %s = %s %s %s;" % (a, b, op, rng.choice([b, str(rng.randint(1, 9)) + "u"])))
        kind = rng.random()
        if kind < 0.35:
            n = rng.randint(2, 5)
            cases = " ".join("case %d: goto L_%d;" % (v, rng.randrange(blocks)) for v in rng.sample(range(8), n))
            default = rng.choice(["default: goto L_%d;" % rng.randrange(blocks), "default: return %s;" %
                                  rng.choice(regs), ""]) if k + 1 < blocks else "default: return r3;"
            lines.append("    switch (%s & 7u) { %s %s }" % (rng.choice(regs), cases, default))
        elif kind < 0.6:
            a, b = rng.choice(regs), rng.choice(regs)
            lines.append("    if (%s %s %s) goto L_%d;" % (a, rng.choice(["<", "==", "!=", ">="]), b,
                                                          rng.randrange(blocks)))
        elif kind < 0.75:
            lines.append("    goto L_%d;" % rng.randrange(blocks))
        elif kind < 0.88 and k + 1 < blocks:
            pass                            # (falls through)
        elif endless and rng.random() < 0.8:
            if rng.random() < 0.25:
                lines.append("    for (;;) bail(r2 ^ %du);" % k)     # (a statement that never ends)
            else:
                lines.append("    goto L_%d;" % rng.randrange(blocks))
        else:
            lines.append("    return %s;" % rng.choice(regs))
    lines.append("    goto L_0;" if endless else "    return r1;")
    lines.append("}")
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")
def test_random_flow_with_switches_structured_computes_the_same(tmp_path):
    rng = random.Random(11)
    originals, results, shapes = [], [], {"goto": 0, "switch cases with statements": 0, "repeated": 0}
    count = 400
    for i in range(count):
        text = random_switch_function(rng, "orig_%d" % i, blocks=rng.randint(5, 10))
        originals.append(text)
        tree = cparse.parse("typedef unsigned int u32;\n" + text.replace("orig_", "rest_"))
        fn = tree.function("rest_%d" % i)
        structure.structure(fn)
        out = cparse.unparse(fn, notes=False, style="readable")
        shapes["goto"] += "goto" in out
        shapes["switch cases with statements"] += "switch" in out and "break;" in out
        shapes["repeated"] += out.count("fuel = fuel - 1;") > text.count("fuel = fuel - 1;")    # (repeat)
        results.append(out)
    source = tmp_path / "flows.c"
    source.write_text("typedef unsigned int u32;\n" + "".join(originals) + "\n".join(results) + "\n")
    lib = tmp_path / "flows.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(source)], check=True)
    so = ctypes.CDLL(str(lib))
    for i in range(count):
        a, b = getattr(so, "orig_%d" % i), getattr(so, "rest_%d" % i)
        for f in (a, b):
            f.restype, f.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 4
        for _ in range(25):
            args = [rng.choice([0, 1, 2, 7, rng.getrandbits(32)]) for _ in range(4)]
            assert a(*args) == b(*args), (i, args, originals[i], results[i])
    assert shapes["switch cases with statements"] > count // 10 and shapes["goto"] < count, shapes
    assert shapes["repeated"] > count // 10, shapes


def test_a_loop_that_never_ends_has_joins():
    """In a loop nothing leaves (a task's), the branches meet where they would in a loop that ends: the
    post-dominators count the jumps back to its head as its ends (structure.endless_ends)."""
    e = structured("L_1: ;\n    r1 = r1 + 1;\n    if (r0 == 0) goto L_2;\n    r2 = r2 + 1;\n    goto L_3;\nL_2: ;\n"
                   "    r3 = r3 + 1;\nL_3: ;\n    r0 = r0 - 1;\n    bail(r1);\n    goto L_1;")
    assert "else" in e and "goto" not in e and "for (;;)" in e, e
    before = structured("    if (r0 == 0) goto L_a;\n    r1 = 1;\n    goto L_b;\nL_a: ;\n    r1 = 2;\nL_b: ;\n"
                        "    r2 = r2 + r1;\n    bail(r2);\n    goto L_b;")
    assert "else" in before and "goto" not in before, before


def test_a_short_way_to_a_return_is_repeated():
    """A jump to a few statements and a return repeats them (structure.TAIL) instead of a goto: each copy
    does what the jump and the code it went to did (here a loop's second exit and a later branch)."""
    body = ("L_loop: ;\n    r0 = r0 - 1;\n    if (r0 == 0) goto L_t;\n    r1 = r1 + r0;\n    if (r1 != 9) goto L_loop;\n"
            "    r2 = r1;\n    if (r2 == 4) goto L_t;\n    return r2;\nL_t: ;\n    r1 = r1 + 7;\n    r2 = r2 ^ r1;\n%s"
            "    return r2;")
    t = structured(body % "")
    assert "goto" not in t and t.count("r2 = r2 ^ r1;") == 2, t
    long = structured(body % "    r3 = r3 + r2;\n    r2 = r2 - r3;\n")
    assert "goto L_t" in long and long.count("r2 = r2 - r3;") == 1, long


def test_blocks_several_ways_reach_follow_an_if_without_join():
    """A branch whose ways never meet (each arm goes round a loop that never ends on its own): the block
    both arms reach, which it dominates, comes after the if, not inside the first arm (a merge)."""
    m = structured("L_top: ;\n    r0 = r0 + 1;\n    if (r1 == 1) goto L_x;\n    if (r2 != 1) goto L_m;\n"
                   "    goto L_top;\nL_x: ;\n    if (r3 == 0) goto L_m;\n    r3 = 0;\n    goto L_top;\nL_m: ;\n"
                   "    r2 = r2 + r0;\n    r1 = r1 ^ r2;\n    goto L_top;")
    assert "goto" not in m and m.count("r1 = r1 ^ r2;") == 1, m
    assert m.index("r1 = r1 ^ r2;") > m.index("r3 = 0;"), m


def never_returns(fn):
    """Whether some block of the function (after its declarations) never reaches a return."""
    body = [s for s in fn.body.body if not isinstance(s, (cparse.Decl, cparse.Note))]
    cut = structure.blocks_of(body)
    return cut is not None and bool(structure.endless_ends(cut[0], structure.reverse_postorder(cut[0])))


@pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")
@pytest.mark.parametrize("guards", ["default", 0])
def test_random_endless_flow_structured_computes_the_same(tmp_path, monkeypatch, guards):
    """Random functions (half of them with switches) whose flow mostly never reaches a return: the fuel
    runs out into bail(), which leaves by longjmp. Structured, compiled next to the originals, called
    through setjmp with random arguments: every one gives the original's result, or bails out with
    the same value. guards 0: early exits wherever they are no deeper (structure.GUARDS)."""
    if guards != "default":
        monkeypatch.setattr(structure, "GUARDS", guards)
    rng = random.Random(23)
    originals, results, shapes = [], [], {"goto": 0, "never returns": 0, "repeated": 0}
    count = 400
    for i in range(count):
        make = random_switch_function if i % 2 else random_function
        text = make(rng, "orig_%d" % i, blocks=rng.randint(5, 10), endless=True)
        originals.append(text)
        tree = cparse.parse("typedef unsigned int u32;\n" + text.replace("orig_", "rest_"))
        fn = tree.function("rest_%d" % i)
        shapes["never returns"] += never_returns(fn)
        structure.structure(fn)
        out = cparse.unparse(fn, notes=False, style="readable")
        shapes["goto"] += "goto" in out
        shapes["repeated"] += out.count("fuel = fuel - 1;") > text.count("fuel = fuel - 1;")    # (repeat)
        results.append(out)
    source = tmp_path / "flows.c"
    source.write_text(BAIL + "".join(originals) + "\n".join(results) + "\n" + "".join(
        bail_caller("orig_%d" % i) + bail_caller("rest_%d" % i) for i in range(count)))
    lib = tmp_path / "flows.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(source)], check=True)
    so = ctypes.CDLL(str(lib))
    for i in range(count):
        a, b = getattr(so, "call_orig_%d" % i), getattr(so, "call_rest_%d" % i)
        for f in (a, b):
            f.restype, f.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 4
        for _ in range(25):
            args = [rng.choice([0, 1, 2, 7, rng.getrandbits(32)]) for _ in range(4)]
            assert a(*args) == b(*args), (i, args, originals[i], results[i])
    assert shapes["never returns"] > count // 2 and shapes["repeated"] > count // 10 and shapes["goto"] < count, shapes


def depth_of(text):
    """The deepest nesting of the function f's body in a text (structure.nesting)."""
    return structure.nesting(cparse.parse("typedef unsigned int u32;\n" + text).function("f").body.body)[0]


ELSE_IF = ("    if (r0 != 1) goto L_2;\n    r1 = 10;\n    goto L_end;\nL_2: ;\n"
           "    if (r0 != 2) goto L_3;\n    r1 = 20;\n    goto L_end;\nL_3: ;\n"
           "    if (r0 != 3) goto L_4;\n    r1 = 30;\n    goto L_end;\nL_4: ;\n"
           "    if (r0 != 4) goto L_end;\n    r1 = 40;\nL_end: ;\n    return r1 + r2;")


def test_an_else_if_chain_comes_out_flat():
    """A chain as compilers lay it out (each test jumps to the next one, its arm falls through and jumps
    to the end): each arm the then arm of its test, the rest of the chain its else (else if), in the
    order of the code; not each test nested in the one before."""
    s = structured(ELSE_IF)
    assert s.count("else if") == 3 and "goto" not in s and depth_of(s) == 1, s
    assert "if (r0 == 1)" in s, s
    assert s.index("r1 = 10") < s.index("r1 = 20") < s.index("r1 = 30") < s.index("r1 = 40"), s
    early = structured("    if (r0 == 0) goto L_x;\n    return 5;\nL_x: ;\n    r1 = r1 + 1;\n    r2 = r2 + r1;\n"
                       "    return r2;")
    assert "if (r0 != 0) return 5;" in early and depth_of(early) == 1, early     # (an arm that returns: an exit)


def test_a_goto_to_where_the_if_falls_anyway_goes():
    """A goto that ends an arm of an if and goes where control falls off the if (here after the if
    around it) is dropped, and its label with it."""
    fn = fn_of("    if (r0 == 0)\n    {\n        r1 = 1;\n        if (r2 == 0)\n        {\n"
               "            r3 = 1;\n            goto L_a;\n        }\n        else\n        {\n"
               "            r3 = 2;\n        }\n    }\nL_a: ;\n    return r1 + r3;")
    body = [s for s in fn.body.body if not isinstance(s, cparse.Decl)]
    out = structure.prune(body)
    fn.body.body[:] = [s for s in fn.body.body if isinstance(s, cparse.Decl)] + out
    text = cparse.unparse(fn, notes=False, style="readable")
    assert "goto" not in text and "L_a" not in text and "r3 = 1;" in text, text


SHARED_EXIT = ("    r1 = r1 + 1;\n    if (r1 != 7) goto L_f;\n    r2 = r2 + r1;\n    if (r2 != 7) goto L_f;\n"
               "    r3 = r3 + r2;\n    if (r3 != 7) goto L_f;\n    r0 = r0 + r3;\n    if (r0 != 7) goto L_f;\n"
               "    r0 = r0 ^ r1;\n    return r0;\nL_f: ;\n    r0 = r0 + 5u;\n    return r0 ^ r1;")
ARMS_TO_RETURN = ("    if (r0 == 0) goto L_e1;\n    r1 = r1 + r0;\n    if (r1 == 0) goto L_e2;\n    r2 = r2 + r1;\n"
                  "    if (r2 == 0) goto L_e3;\n    r3 = r2 + r3;\n    if (r3 == 9) goto L_end;\n"
                  "    r3 = r3 ^ 1u;\n    goto L_end;\nL_e1: ;\n    r3 = 0;\n    goto L_end;\nL_e2: ;\n"
                  "    r3 = 0;\n    goto L_end;\nL_e3: ;\n    r3 = 0;\nL_end: ;\n    return r3;")


def test_a_staircase_of_checks_becomes_early_exits(monkeypatch):
    """Checks that leave for one short block that returns (a stack check's four words), or whose arms
    fall to one return (a check's failure arms, each a copy of the same code): nested one level per
    check, or early exits that repeat the short way; the exits where they take GUARDS levels or more
    off, and never with GUARDS None."""
    s = structured(SHARED_EXIT)
    assert s.count("return r0 ^ r1;") >= 4 and depth_of(s) == 1 and "goto" not in s, s
    a = structured(ARMS_TO_RETURN)
    assert a.count("return r3;") == 4 and depth_of(a) == 1 and "goto" not in a, a
    monkeypatch.setattr(structure, "GUARDS", None)
    plain = structured(SHARED_EXIT)
    assert plain.count("return r0 ^ r1;") == 1 and depth_of(plain) == 4, plain
    plain = structured(ARMS_TO_RETURN)
    assert plain.count("return r3;") == 1 and depth_of(plain) == 4, plain


def test_an_else_holding_comments_and_an_if_prints_as_else_if():
    """In the readable style an else arm of comments (the instructions' notes) and one if is an else if,
    the comments before it, as dread.cmetrics counts it."""
    tree = cparse.parse("typedef unsigned int u32;\nu32 f(u32 a)\n{\n    u32 x;\n    if (a == 1)\n    {\n"
                        "        x = 1;\n    }\n    else\n    {\n        /* 10: nop */\n"
                        "        if (a == 2) { x = 2; } else { x = 3; }\n    }\n    return x;\n}\n")
    text = cparse.unparse(tree.function("f"), style="readable")
    lines = [line.strip() for line in text.splitlines()]
    assert "else if (a == 2)" in lines and lines.index("/* 10: nop */") + 1 == lines.index("else if (a == 2)"), text


def random_checks_function(rng, name, endless=False):
    """A run of checks laid out as compilers lay them out: before each a few statements, then a test
    whose one way does a few statements and comes back (an arm written after the run, as GCC puts an
    error arm, or one in place that the test jumps over), leaves for a block several checks share (a
    short one, which may be repeated, or a long one), or starts an else-if chain (each test jumping to
    the next arm, each arm to the chain's end). endless: the run is the body of a loop that never ends
    (a task's), left by bail() when the fuel runs out or in the shared block."""
    regs, flip = ["r0", "r1", "r2", "r3"], {"<": ">=", ">=": "<", "==": "!=", "!=": "=="}

    def arith():
        a, b = rng.choice(regs), rng.choice(regs)
        return "    %s = %s %s %s;" % (a, b, rng.choice(["+", "-", "^", "&", "|"]),
                                         rng.choice([b, "%du" % rng.randint(1, 9)]))

    def cond():
        return [rng.choice(regs), rng.choice(list(flip)), rng.choice(regs + ["%du" % rng.randint(0, 9)])]
    lines = ["u32 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{",
             "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, fuel = 40;", "L_top: ;"]
    if endless:
        lines += ["    fuel = fuel - 1;", "    if (fuel == 0) bail(r0);"]
    shared, after = rng.random() < 0.7, []
    for i in range(rng.randint(3, 8)):
        lines += [arith() for _ in range(rng.randint(0, 2))]
        kind = rng.choice(["back", "inplace", "shared" if shared else "back", "elseif"])
        a, op, b = cond()
        if kind == "back":
            lines += ["    if (%s %s %s) goto L_e%d;" % (a, op, b, i), "L_r%d: ;" % i]
            after += ["L_e%d: ;" % i] + [arith() for _ in range(rng.randint(1, 2))] + ["    goto L_r%d;" % i]
        elif kind == "inplace":
            lines.append("    if (%s %s %s) goto L_r%d;" % (a, flip[op], b, i))
            lines += [arith() for _ in range(rng.randint(1, 2))] + ["L_r%d: ;" % i]
        elif kind == "shared":
            lines.append("    if (%s %s %s) goto L_fail;" % (a, op, b))
        else:
            arms = rng.randint(2, 4)
            for j in range(arms):
                if j + 1 < arms:
                    a, op, b = cond()
                    lines.append("    if (%s %s %s) goto L_n%d_%d;" % (a, op, b, i, j))
                lines += [arith() for _ in range(rng.randint(1, 2))]
                if j + 1 < arms:
                    lines += ["    goto L_x%d;" % i, "L_n%d_%d: ;" % (i, j)]
            lines.append("L_x%d: ;" % i)
    lines.append("    goto L_top;" if endless else "    return r1 ^ r2;")
    lines += after
    if shared:
        lines.append("L_fail: ;")
        lines += [arith() for _ in range(rng.choice([1, 2, 5]))]
        lines.append("    for (;;) bail(r3 ^ 0x55u);" if endless else "    return r0 ^ 0x55u;")
    lines.append("}")
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")
@pytest.mark.parametrize("guards", ["default", 0])
def test_random_runs_of_checks_structured_compute_the_same(tmp_path, monkeypatch, guards):
    """Random runs of checks (random_checks_function; half of them in a loop that never ends), structured,
    compiled next to the originals, called (through setjmp) with random arguments: every one gives the
    original's result or bails out with the same value. Most come out flat. guards 0: early exits
    wherever they are no deeper, so that the repeats they need run often."""
    if guards != "default":
        monkeypatch.setattr(structure, "GUARDS", guards)
    rng = random.Random(31)
    originals, results, shapes = [], [], {"goto": 0, "flat": 0, "else if": 0}
    count = 300
    for i in range(count):
        text = random_checks_function(rng, "orig_%d" % i, endless=i % 2 == 1)
        originals.append(text)
        tree = cparse.parse("typedef unsigned int u32;\n" + text.replace("orig_", "rest_"))
        fn = tree.function("rest_%d" % i)
        structure.structure(fn)
        out = cparse.unparse(fn, notes=False, style="readable")
        shapes["goto"] += "goto" in out
        shapes["flat"] += structure.nesting(fn.body.body)[0] <= 3
        shapes["else if"] += "else if" in out
        results.append(out)
    source = tmp_path / "checks.c"
    source.write_text(BAIL + "".join(originals) + "\n".join(results) + "\n" + "".join(
        bail_caller("orig_%d" % i) + bail_caller("rest_%d" % i) for i in range(count)))
    lib = tmp_path / "checks.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(source)], check=True)
    so = ctypes.CDLL(str(lib))
    for i in range(count):
        a, b = getattr(so, "call_orig_%d" % i), getattr(so, "call_rest_%d" % i)
        for f in (a, b):
            f.restype, f.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 4
        for _ in range(25):
            args = [rng.choice([0, 1, 2, 7, rng.getrandbits(32)]) for _ in range(4)]
            assert a(*args) == b(*args), (i, args, originals[i], results[i])
    assert shapes["flat"] > count * 0.9 and shapes["else if"] > count // 3, shapes
    assert shapes["goto"] < count // 10, shapes


PAIRS = itertools.count()


def same_results(tmp_path, body, after, prelude=""):
    """The function of body (as fn_of writes it) and the C after a rewrite give the same results for
    random arguments, compiled with the host's C compiler (nothing compared without one; prelude: C
    before both, the functions they call). A library of its own each time: dlopen hands back a library
    already loaded from the same path."""
    if not shutil.which("cc"):
        return
    n = next(PAIRS)
    before = ("u32 orig_%d(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3;\n%s\n}\n"
              % (n, body))
    source = tmp_path / ("pair_%d.c" % n)
    source.write_text("typedef unsigned int u32;\n" + prelude + before + after.replace("u32 f(", "u32 rest_%d(" % n, 1))
    lib = tmp_path / ("pair_%d.so" % n)
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(source)], check=True)
    so = ctypes.CDLL(str(lib))
    a, b = getattr(so, "orig_%d" % n), getattr(so, "rest_%d" % n)
    for f in (a, b):
        f.restype, f.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 4
    rng = random.Random(n)
    for _ in range(400):
        args = [rng.choice([0, 1, 2, 3, 4, 5, 6, 7, rng.getrandbits(32)]) for _ in range(4)]
        assert a(*args) == b(*args), (args, before, after)


def joined(body):
    """The C of body after arm_jumps alone (and the labels nothing names dropped), or None when it
    changed nothing."""
    fn = fn_of(body)
    if not structure.arm_jumps(fn.body.body):
        return None
    fn.body.body[:] = structure.prune_labels(fn.body.body, structure.gotos_in(fn.body.body))
    return cparse.unparse(fn, notes=False, style="readable")


def test_a_jump_to_the_start_of_the_other_arm_is_part_of_the_condition(tmp_path):
    """A check in one arm of an if that jumps to the start of the other arm (a compiler sharing the
    else's code, too long to repeat) joins the if's condition, the statements before it in a comma
    expression: && with what follows the check, || when nothing does, and the other way round. Each
    compiled before and after: the same results."""
    tail = ("L_t: ;\n    r2 = 9;\n    r3 = r3 * 3;\n    r1 = r1 ^ r3;\n    r2 = r2 + r1;\n    r3 = r3 - r2;\n"
            "    r0 = r0 + r3;\n")
    body = ("    if (r0 == 0) goto L_t;\n    r1 = r1 + 1;\n    if (r1 == 5) goto L_t;\n    r2 = 7;\n    goto L_j;\n" +
            tail + "L_j: ;\n    return r2 + r1 + r0;")
    both = structured(body)
    assert "if (r0 != 0 && (r1 = r1 + 1, r1 != 5))" in both and "goto" not in both and "L_t" not in both, both
    same_results(tmp_path, body, both)
    body = ("    if (r0 == 0) goto L_t;\n    r1 = r1 + 1;\n    if (r1 == 5) goto L_t;\n    goto L_j;\n" + tail +
            "L_j: ;\n    return r2 + r1 + r0;")
    alone = structured(body)
    assert "if (r0 == 0 || (r1 = r1 + 1, r1 == 5))" in alone and "else" not in alone and "goto" not in alone, alone
    same_results(tmp_path, body, alone)
    # the label first in the then arm, the jump in the else arm (as the emitter turns some ifs round)
    body = ("    if (r0 == 0)\n    {\nL_t: ;\n        r2 = 9;\n    }\n    else\n    {\n        r1 = r1 + 1;\n"
            "        r3 = r3 ^ r1;\n        if (r1 == 5) goto L_t;\n        r2 = 7;\n    }\n    return r2 + r1 + r3;")
    flipped = joined(body)
    assert "if (r0 == 0 || (r1 = r1 + 1, r3 = r3 ^ r1, r1 == 5))" in flipped and "goto" not in flipped, flipped
    assert "r2 = 7;" in flipped.split("else")[1], flipped
    same_results(tmp_path, body, flipped)


def test_a_jump_to_an_arm_start_stays_where_it_cannot_join_the_condition(tmp_path):
    """Not joined: a statement other than an expression before the check (it cannot go into a comma
    expression), a check whose if has an else, a label further into the arm. And a label another jump
    still names stays when the check joins the condition."""
    assert joined("    if (r0 == 0)\n    {\n        if (r2 != 0) r3 = 1;\n        if (r1 == 5) goto L_t;\n        r2 = 7;\n"
                  "    }\n    else\n    {\nL_t: ;\n        r2 = 9;\n    }\n    return r2 + r1;") is None
    assert joined("    if (r0 == 0)\n    {\n        if (r1 == 5) goto L_t; else r3 = 2;\n        r2 = 7;\n"
                  "    }\n    else\n    {\nL_t: ;\n        r2 = 9;\n    }\n    return r2 + r1;") is None
    assert joined("    if (r0 == 0)\n    {\n        if (r1 == 5) goto L_t;\n        r2 = 7;\n"
                  "    }\n    else\n    {\n        r2 = 8;\nL_t: ;\n        r2 = r2 + 9;\n    }\n    return r2 + r1;") is None
    body = ("    if (r3 == 4) goto L_t;\n    if (r2 != 0) r3 = 1;\n    if (r0 == 0)\n    {\n        r1 = r1 + 1;\n"
            "        if (r1 == 5) goto L_t;\n        r2 = 7;\n    }\n    else\n    {\nL_t: ;\n        r2 = 9;\n    }\n"
            "    return r2 + r1 + r3;")
    kept = joined(body)
    assert "if (r0 == 0 && (r1 = r1 + 1, r1 != 5))" in kept and "L_t: ;" in kept and kept.count("goto") == 1, kept
    same_results(tmp_path, body, kept)


def test_a_jump_after_long_or_calling_statements_stays_a_goto(tmp_path):
    """More than ARM_JUMP_EFFECTS statements before the check, or a call made for its effects alone among
    them, stay as they are: a long comma expression or a call statement in a condition reads worse than
    the goto (a source's call and the test after it, which a compiler threaded). A call whose result is
    assigned joins."""
    def body(before):
        return ("    if (r0 == 0)\n    {\n" + before + "        if (r1 == 5) goto L_t;\n        r2 = 7;\n    }\n"
                "    else\n    {\nL_t: ;\n        r2 = 9;\n    }\n    return r2 + r1 + r3;")
    assert structure.ARM_JUMP_EFFECTS == 2
    assert joined(body("        r1 = r1 + 1;\n        r2 = r2 + 1;\n        r3 = r3 + 1;\n")) is None
    assert joined(body("        sink(r1, 2);\n")) is None
    assert joined(body("        (void)sink(r1, 2);\n")) is None
    two = body("        r1 = r1 + 1;\n        r3 = r3 ^ r1;\n")
    assert "if (r0 == 0 && (r1 = r1 + 1, r3 = r3 ^ r1, r1 != 5))" in joined(two)
    assigned = body("        r1 = sink(r1, 2);\n")
    out = joined(assigned)
    assert "if (r0 == 0 && (r1 = sink(r1, 2), r1 != 5))" in out, out
    same_results(tmp_path, assigned, out, prelude="static u32 sink(u32 a, u32 b)\n{\n    return a * 3u + b;\n}\n")


def test_a_jump_from_before_an_if_to_an_arm_start_is_part_of_its_condition(tmp_path):
    """A jump just before an if, only expression statements between, to the start of one of its arms:
    || where the label begins the then arm, && with the jump's test turned round where it begins the
    else arm; two such jumps one after the other both join (the one further up last)."""
    body = ("    if (r3 == 4) goto L_t;\n    r1 = r1 + 1;\n    if (r0 == 0)\n    {\nL_t: ;\n        r2 = 9;\n    }\n"
            "    else\n    {\n        r2 = 7;\n    }\n    return r2 + r1;")
    then = joined(body)
    assert "if (r3 == 4 || (r1 = r1 + 1, r0 == 0))" in then and "goto" not in then and "L_t" not in then, then
    same_results(tmp_path, body, then)
    body = ("    if (r3 == 4) goto L_t;\n    r1 = r1 + 1;\n    if (r2 < r1) goto L_t;\n    r3 = r3 ^ r2;\n"
            "    if (r0 == 0)\n    {\n        r2 = 7;\n    }\n    else\n    {\nL_t: ;\n        r2 = 9;\n    }\n"
            "    return r2 + r1 + r3;")
    other = joined(body)
    assert "if (r3 != 4 && (r1 = r1 + 1, r2 >= r1) && (r3 = r3 ^ r2, r0 == 0))" in other, other
    assert "goto" not in other and "L_t" not in other, other
    same_results(tmp_path, body, other)


def random_threaded_function(rng, name, endless=False):
    """Checks threaded as a compiler threads them: if (A) goes to a long block (too long to repeat), and
    where A did not hold, statements and one or two more checks go to the same block's start before the
    statements of their own (sometimes none). Laid out either way round: the long block after the checks
    (the emitter makes it the else arm), or first with the checks jumping back into it. Sometimes a jump
    from before the if names the block too. And checks that must stay jumps, their labels named L_stay:
    one after an if statement (which no condition can hold), one into the middle of the long block, and
    an else-if chain whose last arm jumps to the first arm's block (its test would be evaluated twice).
    endless: in a loop that never ends, left by bail() when the fuel runs out."""
    regs, flip = ["r0", "r1", "r2", "r3"], {"<": ">=", ">=": "<", "==": "!=", "!=": "=="}

    def arith():
        a, b = rng.choice(regs), rng.choice(regs)
        return "    %s = %s %s %s;" % (a, b, rng.choice(["+", "-", "^", "&", "|"]),
                                         rng.choice([b, "%du" % rng.randint(1, 9)]))

    def cond():
        return "%s %s %s" % (rng.choice(regs), rng.choice(list(flip)), rng.choice(regs + ["%du" % rng.randint(0, 9)]))
    lines = ["u32 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{",
             "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, fuel = 40;", "L_top: ;"]
    if endless:
        lines += ["    fuel = fuel - 1;", "    if (fuel == 0) bail(r0);"]
    for i in range(rng.randint(1, 4)):
        stay = rng.choice([None] * 6 + ["if", "middle", "chain"])
        block = ("L_stay%d" if stay in ("if", "chain") else "L_long%d") % i
        lines += [arith() for _ in range(rng.randint(0, 2))]
        if rng.random() < 0.2 and stay != "chain":
            lines.append("    if (%s) goto %s;" % (cond(), block))
            lines += [arith() for _ in range(rng.randint(0, 1))]
        checks = []
        n = rng.randint(1, 2)
        for k in range(n):
            checks += [arith() for _ in range(rng.randint(1, 2))]
            last = k == n - 1
            if stay == "if" and last:
                r = rng.choice(regs)
                checks.append("    if (%s) %s = %s + 2u;" % (cond(), r, r))
            checks.append("    if (%s) goto %s;" % (cond(), "L_staymid%d" % i if stay == "middle" and last else block))
        own = [arith() for _ in range(rng.choice([0, 1, 2]))]
        body = [arith() for _ in range(rng.randint(5, 7))]
        long = ["%s: ;" % block] + body[:2] + (["L_staymid%d: ;" % i] if stay == "middle" else []) + body[2:]
        if stay == "chain":
            lines += ["    if (%s) goto %s;" % (cond(), block), "    if (%s) goto L_c%d;" % (cond(), i)]
            lines += [arith() for _ in range(rng.randint(1, 2))] + ["    goto L_x%d;" % i, "L_c%d: ;" % i]
            lines += checks + own + ["    goto L_x%d;" % i] + long
        elif rng.random() < 0.5:
            lines.append("    if (%s) goto %s;" % (cond(), block))
            lines += checks + own + ["    goto L_x%d;" % i] + long
        else:
            lines.append("    if (%s) goto L_c%d;" % (cond(), i))
            lines += long + ["    goto L_x%d;" % i, "L_c%d: ;" % i] + checks + own
        lines.append("L_x%d: ;" % i)
    lines.append("    goto L_top;" if endless else "    return r1 ^ r2 ^ r3;")
    lines.append("}")
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")
def test_random_threaded_checks_structured_compute_the_same(tmp_path, monkeypatch):
    """Random threaded checks (random_threaded_function; half in a loop that never ends), structured,
    compiled next to the originals, called (through setjmp) with random arguments: every one gives the
    original's result or bails out with the same value. The checks join their ifs' conditions: the only
    gotos left are those that must stay (labels L_stay), and most functions change."""
    fired = []
    rewrite = structure.arm_jumps
    monkeypatch.setattr(structure, "arm_jumps", lambda out: fired.append(rewrite(out)) or fired[-1])
    rng = random.Random(47)
    originals, results, left = [], [], []
    count = 300
    for i in range(count):
        text = random_threaded_function(rng, "orig_%d" % i, endless=i % 2 == 1)
        originals.append(text)
        tree = cparse.parse("typedef unsigned int u32;\n" + text.replace("orig_", "rest_"))
        fn = tree.function("rest_%d" % i)
        structure.structure(fn)
        out = cparse.unparse(fn, notes=False, style="readable")
        left += re.findall(r"goto (\w+);", out)
        results.append(out)
    source = tmp_path / "threaded.c"
    source.write_text(BAIL + "".join(originals) + "\n".join(results) + "\n" + "".join(
        bail_caller("orig_%d" % i) + bail_caller("rest_%d" % i) for i in range(count)))
    lib = tmp_path / "threaded.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(source)], check=True)
    so = ctypes.CDLL(str(lib))
    for i in range(count):
        a, b = getattr(so, "call_orig_%d" % i), getattr(so, "call_rest_%d" % i)
        for f in (a, b):
            f.restype, f.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 4
        for _ in range(25):
            args = [rng.choice([0, 1, 2, 7, rng.getrandbits(32)]) for _ in range(4)]
            assert a(*args) == b(*args), (i, args, originals[i], results[i])
    assert sum(fired) > count * 0.9, sum(fired)
    assert left and all(g.startswith("L_stay") for g in left), sorted(set(left))
