"""Loops in their source shape (dread.structure refine_loops, entered, counted; dread.tidy steps;
dread.polish fold_steps): tests at a loop's head as its while condition, a do-while entered with its
condition true as a while or for loop, a step made through a copy as a step of the variable itself, and
an access with its pointer's step behind it as *p++ or *++p. Each shape on its own, what refuses it,
gizmo's strncpy, memcpy and checksum on every build, and property tests: random functions of these
shapes, rewritten, compiled with the host's C compiler beside the originals and run on random
arguments (and on a memory of their own: every address an offset into it), give the original's results
and leave its memory."""
import ctypes
import random
import re
import shutil
import subprocess

import pytest

from conftest import gizmo_dir
from dread import cexpr, cparse, polish, structure, tidy

needs_z3 = pytest.mark.skipif(cexpr.z3 is None, reason="needs z3-solver (the extra 'prove')")
needs_cc = pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")


def fn_of(body, params="u32 a0, u32 a1, u32 a2, u32 a3", decls="    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3;\n"):
    text = "typedef unsigned int u32;\nu32 f(%s)\n{\n%s%s\n}\n" % (params, decls, body)
    return cparse.parse(text).function("f")


def structured(body, **kw):
    fn = fn_of(body, **kw)
    structure.structure(fn)
    return cparse.unparse(fn, notes=False, style="readable")


# --- tests at the head, exits that follow the loop ---

def test_tests_at_a_loops_head_are_its_while_condition():
    """for (;;) { if (a) break; if (b) break; ... } is while (!a && !b) { ... }; a break further in stays
    (strncpy's while (n && *src): the load of *src comes before its test)."""
    out = structured("L_1: ;\n    if (r2 == 0) goto L_e;\n    r3 = r1 + r2;\n    if (r3 == 0) goto L_e;\n"
                     "    r0 = r0 + r3; r2 = r2 - 1;\n    goto L_1;\nL_e: ;\n    return r0;")
    assert "while (r2 != 0)" in out and "if (r3 == 0) break;" in out and "for (;;)" not in out, out
    two = structured("L_1: ;\n    if (r2 == 0) goto L_e;\n    if (r1 == 5) goto L_e;\n    r0 = r0 + r1;"
                     " r2 = r2 - 1; r1 = r1 + 1;\n    goto L_1;\nL_e: ;\n    return r0;")
    assert "while (r2 != 0 && r1 != 5)" in two and "break" not in two, two


def test_an_exit_at_a_loops_head_follows_it():
    """for (;;) { if (c) return x; ... } without another way out is while (!c) { ... } return x; (the
    second loop of strncpy: while (n--) *d++ = 0;); with a break further in, the test stays."""
    out = structured("    r3 = r0 + r1;\nL_1: ;\n    if (r0 == r3) return r2;\n    r2 = r2 + r0; r0 = r0 + 1;\n"
                     "    goto L_1;")
    assert re.search(r"while \(r0 != r3\)\s*\{[^}]*\}\s*return r2;", out) and "for (;;)" not in out, out
    kept = structured("L_1: ;\n    if (r0 == r3) return r2;\n    if (r2 == 7) goto L_e;\n    r2 = r2 + r0;"
                      " r0 = r0 + 1;\n    goto L_1;\nL_e: ;\n    return r1;")
    assert "if (r0 == r3) return r2;" in kept and "while (r2 != 7)" not in kept, kept


def test_a_head_exit_with_statements_and_a_break_follows_the_loop_without_its_break():
    out = structured("L_1: ;\n    if (r0 == 0) goto L_x;\n    r2 = r2 + r0; r0 = r0 - 1;\n    goto L_1;\n"
                     "L_x: ;\n    r1 = r2 + 1;\n    return r1 + r3;")
    assert "while (r0 != 0)" in out and "break" not in out and out.index("while") < out.index("r1 = r2 + 1"), out


# --- do-while loops entered with their condition true ---

@needs_z3
def test_a_do_while_whose_first_test_holds_is_a_for_loop():
    """r3 = 0; do { ...; r3 = r3 + 1; } while (r3 < 10); is a counted loop: its first test holds (0 <
    10), the init right before it and the step its last statement."""
    out = structured("    r3 = 0;\nL_1: ;\n    r0 = r0 + r3;\n    r3 = r3 + 1;\n    if (r3 < 10) goto L_1;\n"
                     "    return r0;")
    assert "for (r3 = 0; r3 < 10; r3 = r3 + 1)" in out and "do" not in out, out


@needs_z3
def test_a_test_before_the_loop_proves_its_entry():
    """if (n == 0) return 0; p = s; e = s + n; do { ...; p = p + 1; } while (p != e); (GCC's loop
    rotation): p != e holds on entry since n != 0; the init moves over a statement that does not touch
    p."""
    out = structured("    if (r1 == 0) return 0;\n    r3 = r0;\n    r2 = r0 + r1;\nL_1: ;\n    r0 = r0 ^ r3;\n"
                     "    r3 = r3 + 1;\n    if (r3 != r2) goto L_1;\n    return r0;")
    assert "for (r3 = r0; r3 != r2; r3 = r3 + 1)" in out and "do" not in out, out
    inside = structured("    if (r1 != 0)\n    {\n        r3 = 0;\n    L_1: ;\n        r0 = r0 + 2;\n"
                        "        r3 = r3 + 1;\n        if (r3 != r1) goto L_1;\n    }\n    return r0;")
    assert "do" not in inside, inside                   # (a label the function keeps: nothing proved)


@needs_z3
def test_a_do_while_whose_entry_is_not_proved_stays():
    unknown = structured("    r3 = r0;\nL_1: ;\n    r1 = r1 + r3;\n    r3 = r3 + 1;\n    if (r3 < 10) goto L_1;\n"
                         "    return r1;")
    assert "do" in unknown and "while (r3 < 10);" in unknown, unknown
    false = structured("    r3 = 20;\nL_1: ;\n    r1 = r1 + r3;\n    r3 = r3 + 1;\n    if (r3 < 10) goto L_1;\n"
                       "    return r1;")
    assert "do" in false, false                         # (the body runs once: 20 < 10 is false)
    memory = structured("    r3 = 0;\nL_1: ;\n    r1 = r1 + r3;\n    r3 = r3 + 1;\n"
                        "    if (*(volatile unsigned char *)r3 != 0) goto L_1;\n    return r1;")
    assert "do" in memory, memory                       # (a condition that reads memory: evaluated once more)
    loaded = structured("    r3 = *(volatile u32 *)r0;\nL_1: ;\n    r1 = r1 + r3;\n    r3 = r3 + 1;\n"
                        "    if (r3 != 0) goto L_1;\n    return r1;")
    assert "do" in loaded, loaded                       # (an init from memory: any value)


@needs_z3
def test_a_branch_before_the_loop_counts_on_both_ways():
    """r3 is 1 or 2 by the if before the loop: r3 != 0 holds on entry either way; on one way only, it
    does not."""
    both = structured("    if (r0 == 4) goto L_a;\n    r3 = 1;\n    goto L_b;\nL_a: ;\n    r3 = 2;\nL_b: ;\n"
                      "    r3 = r3 + 0;\nL_1: ;\n    r1 = r1 + r3;\n    r3 = r3 - 1;\n    if (r3 != 0) goto L_1;\n"
                      "    return r1;")
    assert "do" not in both, both
    one = structured("    if (r0 == 4) goto L_a;\n    r3 = 1;\n    goto L_b;\nL_a: ;\n    r3 = r2;\nL_b: ;\n"
                     "    r3 = r3 + 0;\nL_1: ;\n    r1 = r1 + r3;\n    r3 = r3 - 1;\n    if (r3 != 0) goto L_1;\n"
                     "    return r1;")
    assert "do" in one, one


@needs_z3
def test_a_loop_with_a_continue_keeps_its_step_in_the_body():
    """A continue of a for loop would run the step, one of the while loop does not: the loop stays a
    while loop with its step last."""
    fn = fn_of("    r3 = 0;\n    while (r3 < 10)\n    {\n        if (r0 == 1) continue;\n        r0 = r0 + 1;\n"
               "        r3 = r3 + 1;\n    }\n    return r0;")
    fn.body.body[:] = structure.counted(fn.body.body)
    out = cparse.unparse(fn, notes=False, style="readable")
    assert "for (" not in out and "while (r3 < 10)" in out, out


def test_a_counted_loop_keeps_the_instructions_of_its_init_and_step():
    """The for statement keeps the loop's instruction for its line and remembers the init's and the
    step's, which dread.naming identities names as the variable's definitions (so names carry)."""
    from dread import naming
    text = ("typedef unsigned int u32;\nu32 f(u32 a0)\n{\n    u32 r0 = a0, r3;\n    r3 = 0;  /* 4: movs r3, #0 */\n"
            "    while (r3 < 10)  /* 10: bcc #0x8 */\n    {\n        r0 = r0 + r3;  /* 8: adds r0, r3 */\n"
            "        r3 = r3 + 1;  /* c: adds r3, #1 */\n    }\n    return r0;  /* 12: bx lr */\n}\n")
    fn = cparse.parse(text).function("f")
    before = naming.identities(fn)
    fn.body.body[:] = structure.counted(fn.body.body)
    assert isinstance(fn.body.body[1], cparse.For), cparse.unparse(fn)
    assert naming.identities(fn) == before, (before, naming.identities(fn))
    lines = dict((line, at) for at, line in cparse.unparse_lines(fn))
    assert lines["    for (r3 = 0; r3 < 10; r3 = r3 + 1)"] == 0x4, lines     # (the margin: the init's)


# --- steps through a copy (dread.tidy steps) ---

LIFTED_STEP = """#include "dread.h"
u32 f(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0;
    r1 = r0 + r1;  /* 0: add r1, r0, r1 */
    r3 = 0x0u;  /* 4: mov r3, #0 */
L_8: ;
    { u32 a = r0, w = r0 + 1; r2 = (u32)*(volatile u8 *)a; r0 = w; }  /* 8: ldrb r2, [r0], #1 */
    r3 = r3 + r2;  /* c: add r3, r3, r2 */
    if (r0 != r1) goto L_8;  /* 10: bne #0x8 */
    return r3;  /* 14: bx lr */
}
"""


def test_a_post_indexed_loads_writeback_is_a_step_of_its_register():
    """ldrb r2, [r0], #1 is lifted { a = r0, w = r0 + 1; r2 = *a; r0 = w; }, and propagate made the
    loop's test read w: after the clean-up the load reads r0, r0 = r0 + 1 follows it and the test
    reads r0 again, w gone."""
    fn = cparse.parse(LIFTED_STEP).function("f")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False, style="readable")
    assert re.search(r"r2 = (\(u32\))?\*\(volatile u8 \*\)r0; r0 = r0 \+ 1;", out), out
    assert re.search(r"if \(r0 != r1\w*\) goto", out) and "w" not in out.split("{", 1)[1], out


def test_a_step_whose_copy_another_value_reaches_stays():
    """w read where v no longer holds w's value (v written again after the copy): the test keeps w."""
    text = LIFTED_STEP.replace(
        "    { u32 a = r0, w = r0 + 1; r2 = (u32)*(volatile u8 *)a; r0 = w; }  /* 8: ldrb r2, [r0], #1 */\n",
        "    r4 = r0 + 0x1u;  /* 8: addi a4, a0, 1 */\n    r2 = (u32)*(volatile u8 *)r0;  /* a: lbu */\n"
        "    r0 = r4;  /* c: mv */\n    r0 = r0 ^ r3;  /* e: xor */\n").replace("if (r0 != r1)", "if (r4 != r1)")
    fn = cparse.parse(text).function("f")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False, style="readable")
    assert re.search(r"if \(r4\w* != r1\w*\) goto", out), out


# --- an access and its pointer's step as one (dread.polish fold_steps) ---

def polished(body, decls="    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r12 = 0;\n"):
    text = "#include \"dread.h\"\nu32 f(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n%s%s\n}\n" % (decls, body)
    tree = cparse.parse(text)
    fn = tree.function("f")
    polish.polish(tree, fn)
    return cparse.unparse(fn, notes=False, style="readable")


def test_an_access_and_its_pointers_step_are_one():
    out = polished("    *(volatile u8 *)r3 = (u8)r12; r3 = r3 + 1;\n    r12 = *(volatile u8 *)(r1 + 1); r1 = r1 + 1;\n"
                   "    r2 = *(volatile u8 *)(r0 - 1); r0 = r0 - 1;\n    return r12 + r2;")
    assert "*(volatile u8 *)r3++ = r12;" in out, out
    assert "r12 = *(volatile u8 *)++r1;" in out and "r2 = *(volatile u8 *)--r0;" in out, out
    over = polished("    r12 = *(volatile u8 *)r1;\n    r2 = r2 + 3;\n    r1 = r1 + 1;\n    return r12 + r2;")
    assert "r12 = *(volatile u8 *)r1++;" in over and "r2 += 3;" in over, over     # (over a statement that
                                                                                # does not touch r1)
    typed = polished("    r2 = *p; p = p + 1;\n    q[1] = r2; q = q + 1;\n    return r2;",
                     decls="    u32 r2;\n    const volatile u8 *p = (const volatile u8 *)a0;\n"
                           "    volatile u32 *q = (volatile u32 *)a1;\n")
    assert "r2 = *p++;" in typed and "*++q = r2;" in typed, typed    # (pointers to volatile memory: the
                                                                     # variables themselves are not)


def test_an_access_and_a_step_that_must_stay_apart():
    twice = polished("    *(volatile u8 *)r3 = *(volatile u8 *)(r3 + 2); r3 = r3 + 1;\n    return r3;")
    assert "r3++;" in twice, twice                      # (r3 read twice)
    maybe = polished("    r2 = r0 != 0 ? *(volatile u8 *)r3 : 0; r3 = r3 + 1;\n    return r2 + r3;")
    assert "r3++;" in maybe, maybe                      # (an arm of ?: : not on every path)
    branch = polished("    r2 = *(volatile u8 *)r3;\n    if (r2 == 0) return 0;\n    r3 = r3 + 1;\n"
                      "    return r2 + r3;")
    assert "r3++;" in branch, branch                    # (a test between)
    value = polished("    r2 = r3 + 8; r3 = r3 + 1;\n    return r2 + r3;")
    assert "r3++;" in value, value                      # (no memory access: a value)
    narrow = polished("    r2 = *(volatile u8 *)(h + 1); h = h + 1;\n    return r2 + h;",
                      decls="    u32 r2;\n    u16 h = (u16)a1;\n")
    assert "h++;" in narrow, narrow                     # (++h of a u16 wraps where h + 1 does not: an int of
                                                        # 32 bits; a lifter's u16 register makes int 16 bits)
    taken = polished("    r2 = *(volatile u8 *)r1; r1 = r1 + 1;\n    g(&r1);\n    return r2;",
                     decls="    u32 r1 = a1, r2;\n")
    assert "r1++;" in taken, taken                      # (its address taken: others see it change)
    volatile = polished("    r2 = *p; p = p + 1;\n    return r2;", decls="    u32 r2;\n    u8 *volatile p = (u8 *)a0;\n")
    assert "p++;" in volatile, volatile                 # (a volatile variable: its own accesses keep their order)


# --- gizmo ---

def gizmo_texts(variant, names):
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
def test_gizmos_copies_read_as_loops_with_pointer_steps(variant):
    """gizmo's strncpy, memcpy and checksum on every build. ARM state and Thumb-2 (post- and pre-indexed
    accesses): strncpy is while (n != 0) { c = *++s; if (c == 0) break; *d++ = c; n--; } and while (d !=
    end) *d++ = 0; (its source's while (n && *src) and while (n--)), memcpy and checksum while loops of
    *s++ and *++d. RISC-V (GCC rotated the loops behind a test of the length, the steps separate
    instructions): memcpy and checksum are for loops, for (p = s; p != end; p++), and the byte stores
    of strncpy's second loop *d++ = 0."""
    got = gizmo_texts(variant, ["strncpy", "memcpy", "checksum"])
    copy, mem, sum_ = got["strncpy"], got["memcpy"], got["checksum"]
    if variant != "rv32":
        assert copy.count("while (") == 2 and "for (;;)" not in copy and "do" not in copy, copy
        assert re.search(r"while \(r2 != 0\)", copy) and re.search(r"\w+ = \*\(volatile u8 \*\)\+\+\w+;", copy), copy
        assert re.search(r"while \(r3 != r2_1\)\s*\{\s*\*\(volatile u8 \*\)r3\+\+ = 0;\s*\}\s*(return r0;|\})", copy), copy
        # (the return after the loop, or none: a result no caller reads makes the function void)
        assert re.search(r"while \(r1 != r2_1\)", mem) and re.search(r"\w+ = \*\(volatile u8 \*\)r1\+\+;", mem) \
            and re.search(r"\*\(volatile u8 \*\)\+\+r3 = \w+;", mem), mem
        assert re.search(r"while \(r3 != r1_1\)", sum_) and "*(volatile u8 *)r3++;" in sum_, sum_
    else:
        assert re.search(r"for \(a3 = a0; a3 != a2_1; a3\+\+\)", mem) and "*(volatile u8 *)a1++;" in mem, mem
        assert re.search(r"for \(a2 = a0; a2 != a1_1; a2\+\+\)", sum_), sum_
        assert "*(volatile u8 *)a3++ = 0;" in copy, copy


# --- property tests: random functions of these shapes, compiled by the host ---

def random_loop_function(rng, name):
    """A function in the lifters' style with one loop as a compiler lays out a counted or a guarded
    loop: an optional test before it, an init, the body (a test at its head that leaves, arithmetic, a
    test further in), a step and the test at its end that goes back; fuel bounds every run."""
    regs = ["r0", "r1", "r2"]
    lines = ["u32 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{",
             "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, fuel = 40;"]
    cmp = rng.choice(["<", "<=", "!=", ">", ">=", "=="])
    bound = rng.choice(["%du" % rng.randint(0, 12), "r1", "r2", "0xfffffff0u"])
    mode = rng.random()
    if mode < 0.2:                          # (counted: a constant start, a bound beyond it, or none)
        init = "%du" % rng.randint(0, 3)
        cmp, bound = rng.choice([("<", "%du" % rng.randint(0, 9)), ("!=", "%du" % rng.randint(4, 9)),
                                 ("<=", "%du" % rng.randint(0, 9))])
    elif mode < 0.4:                        # (rotated behind a test of its bound: GCC -O2's for loops)
        lines.append("    if (r1 %s 0u) goto L_end;" % rng.choice(["==", "==", "!="]))
        init, cmp, bound = "0u", rng.choice(["!=", "<"]), "r1"
    else:
        if rng.random() < 0.5:
            lines.append("    if (%s %s %s) goto L_end;" % (rng.choice(["r1", "r2", "r3"]), rng.choice(
                ["==", "!=", "<", ">="]), rng.choice(["0u", "5u", "r2"])))
        init = rng.choice(["0u", "%du" % rng.randint(1, 9), "r1", "r0 + 3u", "r2 - 1u", "a3"])
    lines.append("    r3 = %s;" % init)
    if rng.random() < 0.4:
        lines.append("    r2 = r2 ^ %du;" % rng.randint(1, 9))
    lines.append("L_top: ;")
    if rng.random() < 0.4:
        lines.append("    if (r2 == %du) goto L_end;" % rng.randint(0, 9))
    lines.append("    fuel = fuel - 1;")
    lines.append("    if (fuel == 0) return r0 ^ 0x55u;")
    for _ in range(rng.randint(1, 3)):
        a = rng.choice(regs)
        lines.append("    %s = %s %s %s;" % (a, rng.choice(regs), rng.choice(["+", "^", "-", "|"]),
                                              rng.choice(["r3", "%du" % rng.randint(1, 9)])))
    if rng.random() < 0.3:
        lines.append("    if ((r0 & 7u) == %du) goto L_end;" % rng.randint(0, 7))
    lines.append("    r3 = r3 %s %du;" % ("+" if mode < 0.4 else rng.choice(["+", "-"]),
                                           1 if mode < 0.4 else rng.choice([1, 1, 2, 4])))
    lines.append("    if (r3 %s %s) goto L_top;" % (cmp, bound))
    lines.append("L_end: ;")
    lines.append("    return r0 ^ (r1 << 1) ^ (r2 << 2) ^ (r3 << 3) ^ (fuel << 9);")
    lines.append("}")
    return "\n".join(lines) + "\n"


def run_pairs(tmp_path, prelude, originals, results, count, rng, calls=30, caller=None):
    source = tmp_path / "pairs.c"
    source.write_text(prelude + "".join(originals) + "\n".join(results) + "\n" + (caller or ""))
    lib = tmp_path / "pairs.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(source)], check=True)
    return ctypes.CDLL(str(lib))


@needs_z3
@needs_cc
def test_random_loops_structured_compute_the_same(tmp_path):
    """Random loops (random_loop_function), structured, compiled with the host's C compiler beside the
    originals and called with random and boundary arguments: every one gives the original's result;
    and the shapes come out (for and while loops from rotated ones, tests at the head as conditions)."""
    rng = random.Random(23)
    count, originals, results = 300, [], []
    shapes = {"for": 0, "while": 0, "do": 0}
    for i in range(count):
        text = random_loop_function(rng, "orig_%d" % i)
        originals.append(text)
        fn = cparse.parse("typedef unsigned int u32;\n" + text.replace("orig_", "rest_")).function("rest_%d" % i)
        structure.structure(fn)
        out = cparse.unparse(fn, notes=False, style="readable")
        shapes["for"] += "for (r3 = " in out
        shapes["while"] += bool(re.search(r"^\s*while \(", out, re.M)) and "do" not in out
        shapes["do"] += "do" in out
        results.append(out)
    so = run_pairs(tmp_path, "typedef unsigned int u32;\n", originals, results, count, rng)
    for i in range(count):
        a, b = getattr(so, "orig_%d" % i), getattr(so, "rest_%d" % i)
        for f in (a, b):
            f.restype, f.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 4
        for _ in range(30):
            args = [rng.choice([0, 1, 2, 5, 9, 12, 0x7fffffff, 0x80000000, 0xfffffff0, 0xffffffff,
                                rng.getrandbits(32), rng.randint(0, 16)]) for _ in range(4)]
            assert a(*args) == b(*args), (i, args, originals[i], results[i])
    assert shapes["for"] > count * 0.15 and shapes["while"] > count * 0.1 and shapes["do"] > count * 0.1, shapes


MEMORY = """typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;
static unsigned char memory[2][512];
static unsigned char *mem;
"""


def hosted(fn):
    """fn as the host runs it: every address an offset into memory (each cast to a pointer type gets mem
    in front of its operand), so that the lifted dialect's u32 addresses work on a host of any width."""
    def visit(e):
        if isinstance(e, cparse.Cast) and e.type.rstrip().endswith("*"):
            return cparse.Cast(e.type, cparse.Binary("+", cparse.Name("mem"), visit(e.e)))
        for slot in ("e", "l", "r", "c", "t", "f", "target", "value", "base", "index"):
            x = getattr(e, slot, None)
            if isinstance(x, cparse.Expr):
                setattr(e, slot, visit(x))
        if isinstance(e, cparse.Call):
            e.args = [visit(a) for a in e.args]
        return e

    def stmt(s):
        if isinstance(s, (cparse.ExprStmt, cparse.Return)) and s.e is not None:
            s.e = visit(s.e)
        elif isinstance(s, (cparse.If, cparse.While, cparse.DoWhile)):
            s.cond = visit(s.cond)
        elif isinstance(s, cparse.For):
            s.init, s.cond, s.step = [visit(x) if x is not None else None for x in (s.init, s.cond, s.step)]
        for inner in cparse._nested(s):
            stmt(inner)
    stmt(fn.body)
    return fn


def random_walk_function(rng, name, lifted):
    """A function that walks two pointers through memory: loads and stores of bytes through them, steps
    of one before or after an access (post- and pre-indexed accesses, as the ARM lifter writes them in a
    block, or as separate statements a compiler scheduled), a test that goes back while the pointer has
    not reached its end; each pointer is stepped once a round (two steps of one pointer are an offset
    after the clean-up: *(r0 + 2), r0 += 3). Addresses stay inside 512 bytes for any arguments."""
    lines = ["u32 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{",
             "    u32 r0 = a0 & 0x7fu, r1 = (a1 & 0x7fu) + 0x100u, r2 = a2, r3 = a3, r4 = 0, r5 = 0, fuel = 24;",
             "    r5 = r0 + (a3 & 0x1fu) + 1u;"]
    lines.append("L_top: ;")
    pointers = rng.sample(["r0", "r1"], 2) + [None] * rng.randint(0, 2)
    rng.shuffle(pointers)
    for k, p in enumerate(pointers):          # (each pointer stepped once a round, as a copy loop does)
        kind = rng.randrange(5) if p is not None else 5
        if kind == 0 and lifted:
            lines.append("    { u32 a = %s, w = %s + 1; r2 = (u32)*(volatile u8 *)a; %s = w; }  /* %x: ldrb */" % (p, p, p, 4 * k))
        elif kind == 1 and lifted:
            lines.append("    { u32 a = %s, w = %s + 1; *(volatile u8 *)a = (u8)r3; %s = w; }  /* %x: strb */" % (p, p, p, 4 * k))
        elif kind == 2:
            lines.append("    r4 = %s + 1u;  /* %x: addi */" % (p, 4 * k))
            lines.append("    r2 = r2 + *(volatile u8 *)%s;  /* %x: lbu */" % (p, 4 * k + 1))
            lines.append("    %s = r4;  /* %x: mv */" % (p, 4 * k + 2))
        elif kind == 3:
            lines.append("    *(volatile u8 *)(%s + 1u) = (u8)r2;  /* %x: strb ! */" % (p, 4 * k))
            lines.append("    %s = %s + 1u;  /* %x: strb ! */" % (p, p, 4 * k))
        elif kind == 4:
            lines.append("    r3 = r3 ^ *(volatile u8 *)%s;  /* %x: ldrb */" % (p, 4 * k))
            lines.append("    %s = %s + 1u;  /* %x: add */" % (p, p, 4 * k + 1))
        else:
            lines.append("    r2 = r2 + r3;  /* %x: add */" % (4 * k))
    lines.append("    fuel = fuel - 1u;")
    lines.append("    if (fuel == 0u) return r2 ^ r3;")
    lines.append("    if (r0 %s r5) goto L_top;" % rng.choice(["!=", "<"]))
    lines.append("    return r2 ^ (r3 << 1) ^ (r0 << 2) ^ (r1 << 3) ^ (r4 << 4);")
    lines.append("}")
    return "\n".join(lines) + "\n"


def walk_caller(count, kinds):
    out = ["typedef u32 (*fn_t)(u32, u32, u32, u32);", "static fn_t table[%d][2] = {" % count]
    out += ["    {(fn_t)%s_%d, (fn_t)%s_%d}," % (kinds[0], i, kinds[1], i) for i in range(count)]
    out.append("};")
    out.append("u32 call_pair(u32 i, u32 a0, u32 a1, u32 a2, u32 a3)\n{\n    u32 r[2]; int k, j;\n"
               "    for (k = 0; k < 2; k++)\n    {\n        for (j = 0; j < 512; j++) memory[k][j] = (unsigned char)(j * 7 + a2);\n"
               "        mem = memory[k];\n        r[k] = table[i][k](a0, a1, a2, a3);\n    }\n"
               "    for (j = 0; j < 512; j++) if (memory[0][j] != memory[1][j]) return 0xdeadu;\n"
               "    return r[0] == r[1] ? 0 : 1;\n}\n")
    return "\n".join(out) + "\n"


@needs_cc
def test_random_pointer_walks_cleaned_up_compute_the_same(tmp_path):
    """Random walks of pointers (random_walk_function), in the lifters' style, through every stage
    (dread.tidy, dread.structure, dread.polish), compiled by the host beside the originals with every
    address an offset into a memory of their own (hosted), run on random arguments: the same result and
    the same memory; and the steps come out folded into their accesses (*p++, *++p)."""
    rng = random.Random(31)
    count, originals, results = 200, [], []
    folded = 0
    for i in range(count):
        text = random_walk_function(rng, "orig_%d" % i, lifted=True)
        tree = cparse.parse(MEMORY + text)
        originals.append(cparse.unparse(hosted(tree.function("orig_%d" % i)), notes=False))
        tree = cparse.parse(MEMORY + text.replace("orig_", "rest_"))
        fn = tree.function("rest_%d" % i)
        tidy.tidy(fn)
        structure.structure(fn)
        polish.polish(tree, fn)
        out = cparse.unparse(fn, notes=False, style="readable")
        folded += bool(re.search(r"\+\+\w|\w\+\+[^;]|--\w", out))
        results.append(cparse.unparse(hosted(fn), notes=False))
    so = run_pairs(tmp_path, MEMORY, [o + "\n" for o in originals], results, count, rng,
                   caller=walk_caller(count, ("orig", "rest")))
    call = so.call_pair
    call.restype, call.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 5
    for i in range(count):
        for _ in range(20):
            args = [rng.choice([0, 1, 0x7f, 0x80, 0xff, 0xffffffff, rng.getrandbits(32)]) for _ in range(4)]
            assert call(i, *args) == 0, (i, args, originals[i], results[i])
    assert folded > count * 0.5, folded
