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
