"""dread.structure: gotos as if/else and loops. The shapes one by one (a while, a do-while, an
if/else, two conditions to one target as ||), and a property test: random functions of labels,
arithmetic and gotos (irreducible flow among them), structured, compiled with the host's C compiler
next to the originals and called with random arguments: every one gives the original's result."""
import ctypes
import random
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
    assert "r0 == 1 || r1 == 2" in o and "goto" not in o, o


def random_function(rng, name, blocks=7):
    """A function of labelled blocks: random register arithmetic, then a goto, a conditional goto or a
    return; fuel counts the labels passed, so that every run ends."""
    regs = ["r0", "r1", "r2", "r3"]
    lines = ["u32 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{",
             "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, fuel = 40;"]
    for k in range(blocks):
        lines.append("L_%d: ;" % k)
        lines.append("    fuel = fuel - 1;")
        lines.append("    if (fuel == 0) return r0 ^ %d;" % k)
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
        else:
            lines.append("    return %s;" % rng.choice(regs))
    lines.append("    return r1;")
    lines.append("}")
    return "\n".join(lines) + "\n"


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


def random_switch_function(rng, name, blocks=8):
    """As random_function, with switches among the ways out of a block: a jump table on a register's
    low bits, some cases sharing a target, a default that jumps or returns or none at all."""
    regs = ["r0", "r1", "r2", "r3"]
    lines = ["u32 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{",
             "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, fuel = 40;"]
    for k in range(blocks):
        lines.append("L_%d: ;" % k)
        lines.append("    fuel = fuel - 1;")
        lines.append("    if (fuel == 0) return r0 ^ %d;" % k)
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
        else:
            lines.append("    return %s;" % rng.choice(regs))
    lines.append("    return r1;")
    lines.append("}")
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")
def test_random_flow_with_switches_structured_computes_the_same(tmp_path):
    rng = random.Random(11)
    originals, results, shapes = [], [], {"goto": 0, "switch cases with statements": 0}
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
