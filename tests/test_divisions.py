"""dread.divisions: the runtime routines that divide, found by name and by what they do in the emulator,
their calls written as / and % in readable C, and the helpers GCC's own / and % call made of the image's
routines for the readable check's link (gizmo a32 has no divide instruction: __udivsi3 and
__aeabi_uidivmod are its library's)."""
import os
import shutil

import pytest

from dread import cparse, divisions
from conftest import gizmo_dir

HEAD = "typedef unsigned int u32;\ntypedef int s32;\ntypedef unsigned long long u64;\n"


def test_what_a_division_hands_back():
    """expected: C's quotient (truncated towards 0) and remainder of the arguments as the registers have
    them, in either order, signed or not; classify takes the one division the runs show."""
    assert divisions.expected(1000, 7, "u", "nd") == (142, 6)
    assert divisions.expected(7, 1000, "u", "dn") == (142, 6)
    assert divisions.expected(0xFFFFFC18, 7, "s", "nd") == (-142 & 0xFFFFFFFF, -6 & 0xFFFFFFFF)
    assert divisions.expected(100, 0xFFFFFFF9, "s", "nd") == (-14 & 0xFFFFFFFF, 2)
    runs = [((a, b), divisions.expected(b, a, "u", "nd")) for a, b in divisions.PAIRS]     # (divisor first)
    assert divisions.classify(runs) == ["u", "qr", "dn"]
    runs = [((a, b), (divisions.expected(a, b, "s", "nd")[1], 0)) for a, b in divisions.PAIRS]
    assert divisions.classify(runs) == ["s", "r", "nd"]
    runs = [((a, b), (divisions.expected(a, b, "u", "nd")[0], 99)) for a, b in divisions.PAIRS]
    assert divisions.classify(runs) == ["u", "q", "nd"]
    assert divisions.classify([((a, b), (a + b, 0)) for a, b in divisions.PAIRS]) is None
    calls = []

    def adds(a, b):
        calls.append((a, b))
        return (a + b) & 0xFFFFFFFF, 0
    assert divisions.probe(adds) is None and len(calls) == 1     # (it stops at the first pair)


def _program(variant):
    from dread import readable, regflow
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    return target, image, program, readable.Context(target, image, program, flow)


def test_gizmo_a32_s_routines_by_name_and_by_what_they_do(monkeypatch):
    """gizmo a32's __udivsi3 (the quotient) and __aeabi_uidivmod (both) by their names, and by what they do
    with the names not known (NAMED empty): the same answer; its udiv_var, which divides through
    __udivsi3, is no routine of its own (a wrapper), nor is m3's, which uses udiv."""
    target, image, program, context = _program("a32")
    want = {"__udivsi3": ["u", "q", "nd"], "__aeabi_uidivmod": ["u", "qr", "nd"]}
    assert divisions.find(program, context, target, image) == want
    monkeypatch.setattr(divisions, "NAMED", {})
    assert divisions.find(program, context, target, image) == want
    target, image, program, context = _program("m3")
    assert divisions.find(program, context, target, image) == {}


def rewritten(text, divs, name="f"):
    tree = cparse.parse(HEAD + text)
    fn = tree.function(name)
    changed = divisions.rewrite(tree, fn, divs)
    return changed, cparse.unparse(tree, notes=False)


def test_calls_become_operators():
    """The quotient of a routine that hands back both ((u32)f(a, b), and a 32-bit place given f(a, b)),
    its remainder ((u32)(f(a, b) >> 32)), a one-result routine's result; the operands in the routine's
    order and sign, cast where they are not of it; a division nested in an argument too; a routine's
    extern goes once no call names it; a two-word value kept whole stays a call."""
    divs = {"lib_udiv": ["u", "qr", "dn"], "lib_sdiv": ["s", "qr", "dn"], "__udivsi3": ["u", "q", "nd"],
            "__modsi3": ["s", "r", "nd"]}
    text = ("extern u64 lib_udiv(u32, u32);\nextern u64 lib_sdiv(u32, u32);\nextern u32 __udivsi3(u32, u32);\n"
            "extern u32 __modsi3(u32, u32);\nextern u32 g(u32);\n"
            "u32 f(u32 a, u32 b, s32 c)\n{\n    u32 q, r, t;\n    u64 w;\n"
            "    q = (u32)lib_udiv(b, a);\n    r = (u32)(lib_udiv(10u, a) >> 32);\n    t = lib_sdiv(250u, c);\n"
            "    g(__udivsi3(a, (u32)lib_udiv(3u, b)));\n    g(__modsi3(c, 7));\n    w = lib_udiv(b, a);\n"
            "    return q + r + t + (u32)w;\n}\n")
    changed, out = rewritten(text, divs)
    assert changed
    for line in ("q = a / b;", "r = a % 10u;", "t = c / 250;", "g(a / (b / 3u));", "g(c % 7);",
                 "w = lib_udiv(b, a);"):
        assert line in out, (line, out)
    assert "extern u64 lib_udiv(u32, u32);" in out and "lib_sdiv" not in out and "__udivsi3" not in out, out
    assert "__modsi3" not in out, out
    signed = rewritten("extern u64 lib_sdiv(u32, u32);\nu32 f(u32 a)\n{\n    return (u32)lib_sdiv(7u, a);\n}\n", divs)[1]
    assert "return (s32)a / (s32)7u;" in signed or "return (s32)a / 7;" in signed, signed


def test_a_routine_that_divides_keeps_its_own_calls():
    """__aeabi_uidivmod's C calls __udivsi3: written r0 / r1, GCC would call __aeabi_uidiv, which the
    readable check's link makes of __aeabi_uidivmod (support_source), and the routine would call itself."""
    divs = {"__udivsi3": ["u", "q", "nd"], "__aeabi_uidivmod": ["u", "qr", "nd"]}
    text = ("extern u32 __udivsi3(u32, u32);\nu64 __aeabi_uidivmod(u32 r0, u32 r1)\n{\n"
            "    return __udivsi3(r0, r1);\n}\n")
    changed, out = rewritten(text, divs, name="__aeabi_uidivmod")
    assert not changed and "__udivsi3(r0, r1)" in out


def test_the_helpers_gcc_calls_are_made_of_the_image_s_routines():
    """Each helper the architecture's compiler calls for / and % that the image lacks, made of a routine
    of the same sign in its own order (the divisor first: d, n): on ARM the RTABI's four, on RISC-V
    libgcc's; one the image has is not made; nothing without a routine."""
    divs = {"lib_udiv": ["u", "qr", "dn"], "lib_sdiv": ["s", "qr", "dn"]}
    arm = divisions.support_source("arm", divs, set())
    for line in ("u32 __aeabi_uidiv(u32 n, u32 d) { return (u32)lib_udiv(d, n); }",
                 "u64 __aeabi_uidivmod(u32 n, u32 d) { return lib_udiv(d, n); }",
                 "u32 __aeabi_idiv(u32 n, u32 d) { return (u32)lib_sdiv(d, n); }",
                 "u64 __aeabi_idivmod(u32 n, u32 d) { return lib_sdiv(d, n); }",
                 "extern u64 lib_udiv(u32, u32);", '#include "dread.h"'):
        assert line in arm, (line, arm)
    riscv = divisions.support_source("riscv", divs, {"__divsi3"})
    for line in ("u32 __udivsi3(u32 n, u32 d) { return (u32)lib_udiv(d, n); }",
                 "u32 __umodsi3(u32 n, u32 d) { return (u32)(lib_udiv(d, n) >> 32); }",
                 "u32 __modsi3(u32 n, u32 d) { return (u32)(lib_sdiv(d, n) >> 32); }"):
        assert line in riscv, (line, riscv)
    assert "__divsi3(u32" not in riscv
    assert divisions.support_source("arm", {}, set()) == "" and divisions.support_source("msp430", divs, set()) == ""
    a32 = divisions.support_source("arm", {"__udivsi3": ["u", "q", "nd"], "__aeabi_uidivmod": ["u", "qr", "nd"]},
                                   {"__udivsi3", "__aeabi_uidivmod"})
    assert "u32 __aeabi_uidiv(u32 n, u32 d) { return (u32)__aeabi_uidivmod(n, d); }" in a32, a32


@pytest.mark.slow
@pytest.mark.compiles
def test_gizmo_a32_s_divisions_through_the_gate(tmp_path):
    """dread readable --check on a copy of gizmo a32: fmt_dec's remainder by 10 is written as %, its
    quotient, which the compiler multiplied out, as / (divisions.constants), and the readable table takes
    it and print_status, which calls it; __aeabi_uidivmod itself stays readable (its
    own call of __udivsi3 stays); the link has the __aeabi_uidiv a C / would call (made of
    __aeabi_uidivmod). udiv_var, whose C is the program's own (not in the lifted table), prints its
    quotient as / when asked for."""
    import subprocess
    import sys
    import dread
    from dread.target import Target
    from conftest import GIZMO
    shutil.copytree(GIZMO, tmp_path / "gizmo", ignore=shutil.ignore_patterns("build"))
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir("a32"), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, tmp_path / "gizmo" / "a32" / "build" / cache)
    root = str(tmp_path / "gizmo" / "a32")
    env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(dread.__file__))))
    r = subprocess.run([sys.executable, "-m", "dread.cli", "readable", "--check", "--target", root], cwd=root,
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    target = Target.load(root)
    readable_names = open(target.image().table("readable")).read().split()
    assert {"fmt_dec", "print_status", "__aeabi_uidivmod"} <= set(readable_names), readable_names
    support = os.path.join(root, "build", "readable-check", "main", "support", divisions.SUPPORT)
    assert "__aeabi_uidiv(u32 n, u32 d)" in open(support).read()
    out = subprocess.run([sys.executable, "-m", "dread.cli", "readable", "--target", root, "fmt_dec"], cwd=root,
                         env=env, capture_output=True, text=True).stdout
    assert "% 10" in out and "__aeabi_uidivmod" not in out.split("\n", 1)[1], out
    assert "/= 10;" in out and "0xcccccccd" not in out, out
    out = subprocess.run([sys.executable, "-m", "dread.cli", "readable", "--target", root, "udiv_var"], cwd=root,
                         env=env, capture_output=True, text=True).stdout
    assert "r0 / r1" in out and "__udivsi3" not in out.split("\n", 1)[1], out


@pytest.mark.slow
@pytest.mark.compiles
def test_a_function_whose_divisions_fail_keeps_the_calls(tmp_path, monkeypatch):
    """Where a function's / and % fail its cases (on the private real-life test: a case that stubs the
    routine and logs each call, which the compiler's / does not make where it knows the divisor), the
    check tries it once more with its divisions as calls (the readable_types table's divcalls). Here
    fmt_dec's rewrite is made wrong on purpose (its % written as /), so that its cases fail: it comes
    back readable with __aeabi_uidivmod called, and the table lists it."""
    import json
    from dread import cparse as C, readable
    from dread.target import Target
    from conftest import GIZMO
    shutil.copytree(GIZMO, tmp_path / "gizmo", ignore=shutil.ignore_patterns("build"))
    root = tmp_path / "gizmo" / "a32"
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir("a32"), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, root / "build" / cache)
    real = divisions.rewrite

    def wrong(tree, fn, divs, int_bits=None):
        changed = real(tree, fn, divs, int_bits)
        if changed and fn.name == "fmt_dec":
            from dread import polish
            for st in polish._all_statements(fn.body.body):
                for e in polish._stmt_exprs(st):
                    for x in polish._walk(e):
                        if isinstance(x, C.Binary) and x.op == "%":
                            x.op = "/"
        return changed
    monkeypatch.setattr(divisions, "rewrite", wrong)
    target = Target.load(str(root))
    image = target.image()
    lines = []
    readable.check(target, image, out=lines.append, jobs=4)
    said = "\n".join(lines)
    assert "calls    fmt_dec" in said, said[-3000:]
    assert "fmt_dec" in open(image.table("readable")).read().split(), said[-2000:]
    plan = json.load(open(image.table("readable_types")))
    assert plan.get("divcalls") == ["fmt_dec"], plan.get("divcalls")



def test_a_multiplier_is_a_division_only_where_the_numbers_prove_it():
    """magic_divisor: GCC's multipliers for / 3, / 10, / 100, / 1000 with their shifts give the divisors
    (each checked on random values and on the last three runs of d values below 2**32); clang -O2's for
    / 150 holds for the 15-bit values it was made for and not for every 32-bit one (1309892841 differs).
    And whatever it returns holds: every 12-bit value of random multipliers near a reciprocal, tried."""
    import random
    for m, k, d in ((0xaaaaaaab, 33, 3), (0xcccccccd, 35, 10), (0x51eb851f, 37, 100), (0x10624dd3, 38, 1000)):
        assert divisions.magic_divisor(m, k) == d, (m, k)
        rng = random.Random(d)
        for x in [rng.getrandbits(32) for _ in range(20000)] + [0, d - 1, d] + list(range(2 ** 32 - 3 * d, 2 ** 32)):
            assert (x * m) >> k == x // d, (m, k, x)
    assert divisions.magic_divisor(0x1b4e81c, 32, bits=15) == 150
    assert divisions.magic_divisor(0x1b4e81c, 32) is None
    x = 1309892841
    assert (x * 0x1b4e81c) >> 32 != x // 150
    assert divisions.magic_divisor(0xcccccccd, 32) is None          # (x * 0.8: no whole divisor)
    rng = random.Random(7)
    taken = 0
    for _ in range(1500):
        d, k = rng.randint(2, 4095), rng.randint(32, 44)
        m = -(-(1 << k) // d) + rng.randint(-2, 2)
        got = divisions.magic_divisor(m, k, bits=12)
        if got is not None:
            taken += 1
            assert all((x * m) >> k == x // got for x in range(1 << 12)), (m, k, got)
    assert taken > 300, taken


CONSTS = HEAD + """u32 f(u32 r0, u32 r4, s32 r6)
{
    u32 r1, r5;
    r4 = (u32)((u64)r4 * 0xcccccccdu >> 32) >> 3;
    r1 = (r0 >> 16) & 0x7fffu;
    r5 = r1 - (u32)((u64)r1 * 0x1b4e81cu >> 32) * 150;
    return r4 + r5 + (u32)((u64)r0 * 0x1b4e81cu >> 32) + ((u32)((u64)r6 * 0xcccccccdu >> 32) >> 3);
}
"""


@pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")
def test_divisions_by_constants_are_written_as_such(tmp_path):
    """constants: the high word of x times GCC's multiplier for / 10, shifted, is x / 10; x minus clang's
    quotient by 150 times 150 is x % 150 where x is a 15-bit value (its one definition masks it); the same
    multiplier on a 32-bit parameter, and a product of a signed value, stay. Compiled before and after
    with the host's C compiler: the same results."""
    import ctypes
    import random
    import subprocess
    tree = cparse.parse(CONSTS)
    fn = tree.function("f")
    assert divisions.constants(tree, fn)
    out = cparse.unparse(fn, notes=False)
    assert "r4 = r4 / 10;" in out and "r5 = r1 % 150;" in out, out
    assert "(u32)((u64)r0 * 0x1b4e81cu >> 32)" in out and "(u64)r6 * 0xcccccccdu" in out, out
    tree2 = cparse.parse(CONSTS)
    assert divisions.has_constants(tree2, tree2.function("f"))
    assert "/ 10" not in cparse.unparse(tree2.function("f"), notes=False)       # (asked, not changed)
    source = tmp_path / "consts.c"
    source.write_text(HEAD + CONSTS[len(HEAD):].replace("u32 f(", "u32 orig(") + out.replace("u32 f(", "u32 rest("))
    lib = tmp_path / "consts.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(source)], check=True)
    so = ctypes.CDLL(str(lib))
    a, b = so.orig, so.rest
    for f in (a, b):
        f.restype, f.argtypes = ctypes.c_uint32, [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32]
    rng = random.Random(3)
    for _ in range(20000):
        args = [rng.getrandbits(32), rng.choice([rng.getrandbits(32), 9, 10, 0xFFFFFFFF]), rng.getrandbits(31)]
        assert a(*args) == b(*args), args


@pytest.mark.slow
@pytest.mark.compiles
def test_a_function_whose_constant_division_fails_keeps_the_multiplication(tmp_path, monkeypatch):
    """Where a function's division by a constant fails its cases (on GCC -Os for ARMv4T a C % 150 becomes
    a call of __aeabi_uidivmod the original never makes), the check tries it once more with its
    divisions as written in the machine code (the readable_types table's divcalls): here fmt_dec's / 10 is
    made / 9 on purpose; it comes back readable with the multiplication, and the table lists it."""
    from dread import cparse as C, readable
    from dread.target import Target
    from conftest import GIZMO
    shutil.copytree(GIZMO, tmp_path / "gizmo", ignore=shutil.ignore_patterns("build"))
    root = tmp_path / "gizmo" / "a32"
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir("a32"), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, root / "build" / cache)
    real = divisions.constants

    def wrong(tree, fn, int_bits=None):
        changed = real(tree, fn, int_bits)
        if changed and fn.name == "fmt_dec":
            from dread import polish
            for st in polish._all_statements(fn.body.body):
                for e in polish._stmt_exprs(st):
                    for x in polish._walk(e):
                        if isinstance(x, C.Binary) and x.op == "/" and isinstance(x.r, C.Num) and x.r.value == 10:
                            x.r = C.Num("9")
        return changed
    monkeypatch.setattr(divisions, "constants", wrong)
    target = Target.load(str(root))
    image = target.image()
    lines = []
    readable.check(target, image, out=lines.append, jobs=4)
    said = "\n".join(lines)
    assert "calls    fmt_dec" in said, said[-3000:]
    assert "fmt_dec" in open(image.table("readable")).read().split(), said[-2000:]
    import json
    table = json.load(open(image.table("readable_types")))
    assert "fmt_dec" in table.get("divcalls", []), sorted(table)

