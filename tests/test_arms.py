"""Merged arms and hoisted statements (dread.structure arms_pass, merged_arms, hoisted; dread.tidy's
hoisting at the end of the clean-up): arms of the same code as one condition with ||, what both arms
of an if begin or end with in front of or behind it, a run of exits with one body as one; what refuses
each (a test with an effect, a statement that writes what the test reads, a label in front of it); and
a property test: random functions of such ifs, rewritten, compiled with the host's C compiler beside
the originals and called with random arguments, give the original's results and make the same calls in
the same order."""
import ctypes
import random
import re
import shutil
import subprocess

import pytest

from dread import cexpr, cparse, structure, tidy

needs_z3 = pytest.mark.skipif(cexpr.z3 is None, reason="needs z3-solver (the extra 'prove')")
needs_cc = pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")

HEAD = ("typedef unsigned int u32;\nextern u32 probe(u32, u32);\nu32 f(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n"
        "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0;\n")


def armed(body):
    fn = cparse.parse(HEAD + body + "\n}\n").function("f")
    fn.body.body[:] = structure.arms_pass(fn.body.body)[0]
    return cparse.unparse(fn, notes=False, style="readable")


def test_neighbouring_arms_of_one_code_are_one_condition():
    """eTaskGetState's else if (list == delayed) r = 2; else if (list == overflow) r = 2; (one block of
    code the compiler reached from both tests, written in each arm) is one arm with ||; the test of
    an arm that is the last else's code goes where it has no effect."""
    out = armed("    if (r0 == 1) { r4 = 1; } else if (r5x == r1) { r4 = 2; } else if (r5x == r2) { r4 = 2; }"
                " else { r4 = 3; }\n    return r4;".replace("r5x", "r3"))
    assert "else if (r3 == r1 || r3 == r2)" in out and out.count("r4 = 2;") == 1, out
    last = armed("    if (r0 == 1) { r4 = 1; } else if (r3 == r2) { r4 = 2; } else { r4 = 2; }\n    return r4;")
    assert "r3 == r2" not in last and last.count("r4 = 2;") == 1 and "else" in last, last
    called = armed("    if (r0 == 1) { r4 = 1; } else if (probe(r3, 0) == r2) { r4 = 2; } else { r4 = 2; }\n"
                   "    return r4;")
    assert "probe(r3, 0)" in called, called                 # (a test with an effect stays)


def test_statements_both_arms_begin_or_end_with_are_hoisted():
    """prvCheckExpectedTimeIsWithinAnAcceptableMargin's r4 = r1 + 7; in both arms (an IT block's
    statement between written into each) goes in front of the if; a store both arms end with behind
    it; an arm left empty turns the if round."""
    out = armed("    if (r0 >= r1) { r4 = r1 + 7; } else { r4 = r1 + 7; probe(1, 2); }\n"
                "    if (r4 < r0) probe(3, 4);\n    return r0;")
    assert out.index("r4 = r1 + 7;") < out.index("if (r0 < r1)") and out.count("r4 = r1 + 7;") == 1, out
    tail = armed("    if (r0 == 2) { r1 = r1 + 1; probe(r1, 9); } else { r2 = 3; probe(r1, 9); }\n    return r1 + r2;")
    assert tail.count("probe(r1, 9);") == 1 and tail.index("}") < tail.index("probe(r1, 9);"), tail


def test_a_statement_stays_in_its_arms_where_moving_it_would_change_a_value():
    writes = armed("    if (r0 >= r1) { r0 = r0 + 1; r2 = 1; } else { r0 = r0 + 1; r2 = 2; }\n    return r2;")
    assert writes.count("r0 = r0 + 1;") == 2, writes        # (it writes what the test reads)
    effect = armed("    if (probe(r0, 0) != 0) { r3 = *(volatile u32 *)r1; r2 = 1; } else { r3 = *(volatile u32 *)r1; "
                   "r2 = 2; }\n    return r2 + r3;")
    assert effect.count("r3 = *(volatile u32 *)r1;") == 2, effect   # (the test calls: the load ran after it)
    value = armed("    if (probe(r0, 0) != 0) { r3 = r1 + 1; r2 = 1; } else { r3 = r1 + 1; r2 = 2; }\n    return r2 + r3;")
    assert value.count("r3 = r1 + 1;") == 1 and value.index("r3 = r1 + 1;") < value.index("probe"), value
    # (a value of variables the call cannot reach goes in front all the same)
    loads = armed("    if (*(volatile u32 *)r1 != 0) { probe(1, 1); r2 = 1; } else { probe(1, 1); r2 = 2; }\n"
                  "    return r2;")
    assert loads.count("probe(1, 1);") == 2, loads          # (the test loads, the statement calls)
    one = armed("    if (r0 >= r1) { r3 = 1; r2 = 1; }\n    return r2 + r3;")
    assert one.count("r3 = 1;") == 1 and "if (r0 >= r1)" in one, one   # (no else: it runs on one way)


def test_a_statement_written_once_keeps_the_definitions_of_its_copies():
    """Copies of one statement at two instructions (GCC -O0 calls vTaskResume at the end of both arms of
    prvSuspendedTaskTimerTestCallback) written once: the statement kept stands for the other
    (cparse.copy_of), and dread.naming identities counts both instructions as its variable's
    definitions, so a name made for the C before carries."""
    from dread import naming
    text = (HEAD + "    if (r0 == 2)  /* 10: bne */\n    {\n        r1 = 7;  /* 12: movs r1, #7 */\n"
            "        r4 = probe(r1, 0);  /* 14: bl probe */\n    }\n    else\n    {\n        r2 = 3;  /* 18: movs r2, #3 */\n"
            "        r4 = probe(r1, 0);  /* 1a: bl probe */\n    }\n    return r4 + r2;  /* 1e: bx lr */\n}\n")
    fn = cparse.parse(text).function("f")
    before = naming.identities(fn)
    fn.body.body[:] = structure.arms_pass(fn.body.body)[0]
    out = cparse.unparse(fn, notes=False, style="readable")
    assert out.count("probe(r1, 0)") == 1, out
    assert naming.identities(fn)["r4"] == before["r4"] == "r4 entry 0x14 0x1a", (before, naming.identities(fn))


def test_exits_with_one_body_are_one_if():
    out = armed("    if (r0 == 0) return r1;\n    if (r2 == 5) return r1;\n    return r3;")
    assert "if (r0 == 0 || r2 == 5) return r1;" in out, out
    falls = armed("    if (r0 == 0) r1 = 2;\n    if (r2 == 5) r1 = 2;\n    return r1;")
    assert falls.count("r1 = 2;") == 2, falls              # (an arm that falls through: both may run)


IT = """#include "dread.h"
extern u32 now(void);
u32 f(u32 r0, u32 r1)
{
    u32 r2 = 0, r3 = 0, r4 = 0, n = 0, z = 0, c = 0, v = 0;
    (void)n; (void)z; (void)c; (void)v;
    r4 = r1;  /* 0: mov r4, r1 */
    r0 = now() - r0;  /* 2: bl now */
    { u32 x = r0, y = r1; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 6: cmp r0, r1 */
    /* 8: itt lo */
    if (!c) { r2 = 0x2f7u; }  /* a: movwlo r2, #0x2f7 */
    if (!c) { r3 = 0x200009d0u; }  /* c: movtlo r3, #0x2000 */
    r4 = r4 + 0x7u;  /* e: add.w r4, r4, #7 */
    /* 10: it lo */
    if (!c) { *(volatile u32 *)((r3 + 0)) = (u32)r2; }  /* 12: strlo r2, [r3] */
    { u32 x = r4, y = r0; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 14: cmp r4, r0 */
    /* 16: it lo */
    if (!c) { *(volatile u32 *)0x200009d0u = 0x2ffu; }  /* 18: strlo */
    return r0;  /* 1a: pop */
}
"""


@needs_z3
def test_the_clean_up_hoists_what_an_it_block_wrote_into_both_arms():
    """The load-free statement between two IT blocks (merge_ifs writes it into both arms of the one if,
    where the clean-up uses it on each way) goes back in front of the if once the clean-up is done,
    and the next test takes it in: if (r1 + 7 < r0), the source's xActualBlockTime > xExpectedBlockTime
    + xAllowableMargin."""
    fn = cparse.parse(IT).function("f")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False, style="readable")
    assert out.count("+ 7") == 1 and "else" not in out, out
    assert re.search(r"if \(r1 \+ 7 < r0\w*\)|if \(r0\w* > r1 \+ 7\)", out), out


# --- gizmo ---

def gizmo_texts(variant, names):
    from conftest import gizmo_dir
    from dread import lift, readable, regflow
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    context = readable.Context(target, image, program)
    return {n: re.sub(r"\s*/\*.*?\*/", "", readable.transform(n, lifter.lift(program.by_name[n]).text,
                                                              context=context)) for n in names}


@needs_z3
def test_gizmos_shared_statements_leave_their_arms():
    """RISC-V's script_run sets each case's sum to 0 on both ways of its test of the length (GCC
    duplicated the move into the loop's way and the empty one): once, in front of the test; its
    read_line ends both ways with the result -1: once, behind the if. Thumb-2 with the FPU's
    apply_config advances its pointer in both arms of an IT block's if: once, in front of it. (Case 1 of
    script_run keeps its two: its loop's way sets the index first, a statement that is not moved.)"""
    rv = gizmo_texts("rv32", ["script_run", "read_line"])
    assert len(re.findall(r"a0_4 = 0;\s*if \(a1 != 0\)", rv["script_run"])) >= 3, rv["script_run"]
    assert rv["read_line"].count("= -1;") == 1, rv["read_line"]
    m4 = gizmo_texts("m4", ["apply_config"])["apply_config"]
    assert len(re.findall(r"\br2\+\+;", m4)) == 1 and re.search(r"r2\+\+;\s*if \(", m4), m4


# --- property test ---

STMTS = ("r0 = r0 + r1;", "r1 = r1 ^ 5;", "r2 = r2 - r3;", "r3 = r3 | r0;", "probe(r0, r2);", "r4 = probe(r1, 7);",
         "r2 = r2 + 1;", "r4 = r4 ^ r2;")
CONDS = ("r0 < r1", "r2 == 3", "r3 != r0", "(r1 & 1) == 0", "r4 >= r2", "probe(r3, 1) == 0")


def random_arms_function(rng, name):
    """A function of ifs whose arms share code: if / else with the same statements first or last (some
    writing what the test reads, some tests calling), else if chains with neighbouring arms of one code,
    runs of exits with one body; calls to probe log their order and arguments."""
    lines = ["u32 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{", "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0;"]

    def some(n):
        return " ".join(rng.choice(STMTS) for _ in range(n))
    for _ in range(rng.randint(2, 5)):
        kind = rng.randrange(4)
        cond = rng.choice(CONDS)
        if kind == 0:
            shared, tail = some(rng.randint(1, 2)), some(rng.randint(0, 1))
            lines.append("    if (%s) { %s %s %s } else { %s %s %s }" % (cond, shared, some(rng.randint(0, 2)), tail,
                                                                       shared, some(rng.randint(0, 2)), tail))
        elif kind == 1:
            x, y = some(rng.randint(1, 2)), some(rng.randint(1, 2))
            arms = [rng.choice([x, y]) for _ in range(rng.randint(2, 4))]
            text = "    if (%s) { %s }" % (cond, arms[0])
            for a in arms[1:]:
                text += " else if (%s) { %s }" % (rng.choice(CONDS), a)
            if rng.random() < 0.6:
                text += " else { %s }" % rng.choice([x, y])
            lines.append(text)
        elif kind == 2:
            ret = "return r%d ^ %du;" % (rng.randrange(5), rng.randrange(9))
            lines.append("    if (%s) { %s }" % (cond, ret))
            lines.append("    if (%s) { %s }" % (rng.choice(CONDS), ret if rng.random() < 0.8 else "r1 = 2;"))
        else:
            lines.append("    " + some(1))
    lines.append("    return r0 ^ (r1 << 1) ^ (r2 << 2) ^ (r3 << 3) ^ (r4 << 4);")
    lines.append("}")
    return "\n".join(lines) + "\n"


PROBE = ("typedef unsigned int u32;\nstatic u32 trace;\nstatic u32 probe(u32 a, u32 b)\n{\n"
         "    trace = trace * 33u + a + 7u * b;\n    return trace & 3u;\n}\n")


def caller(name):
    return ("u32 call_%s(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n    u32 r;\n    trace = 0;\n    r = %s(a0, a1, a2, a3);\n"
            "    return r ^ (trace * 3u);\n}\n" % (name, name))


@needs_cc
def test_random_arms_merged_and_hoisted_compute_the_same(tmp_path):
    """Random functions of ifs that share code (random_arms_function), their arms merged and statements
    hoisted (arms_pass), compiled with the host's C compiler beside the originals and called with
    random arguments: the same result and the same calls in the same order (probe's trace); and the
    rewrites happen (fewer statements in most)."""
    rng = random.Random(17)
    count, originals, results, fewer = 300, [], [], 0
    for i in range(count):
        text = random_arms_function(rng, "orig_%d" % i)
        originals.append(text)
        fn = cparse.parse(PROBE + text.replace("orig_", "arms_")).function("arms_%d" % i)
        before = cparse.unparse(fn, notes=False, style="readable")
        fn.body.body[:] = structure.arms_pass(fn.body.body)[0]
        out = cparse.unparse(fn, notes=False, style="readable")
        fewer += out.count(";") < before.count(";")
        results.append(out)
    source = tmp_path / "arms.c"
    source.write_text(PROBE + "".join(originals) + "\n".join(results) + "\n" + "".join(
        caller("orig_%d" % i) + caller("arms_%d" % i) for i in range(count)))
    lib = tmp_path / "arms.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(source)], check=True)
    so = ctypes.CDLL(str(lib))
    for i in range(count):
        a, b = getattr(so, "call_orig_%d" % i), getattr(so, "call_arms_%d" % i)
        for f in (a, b):
            f.restype, f.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 4
        for _ in range(30):
            args = [rng.choice([0, 1, 2, 3, 5, 0x7fffffff, 0x80000000, 0xffffffff, rng.getrandbits(32)]) for _ in range(4)]
            assert a(*args) == b(*args), (i, args, originals[i], results[i])
    assert fewer > count * 0.5, fewer
