"""dread.bound: a bound call (dread.lift's block that binds registers, calls in an asm and takes them back)
written as one statement of dread.h's BOUND, only where the block is the lifter's as it writes it; and
dread.h's macros make the same instructions as the block (gizmo a32's compiler)."""
import os
import shutil
import subprocess

import pytest

from dread import bound, cparse

HEAD = "typedef unsigned int u32;\n"
CALL = ('    { register u32 x0 __asm__("r0") = delay, x1 __asm__("r1") = r1, x2 __asm__("r2") = 5, '
        'x5 __asm__("r5") = arg; __asm__ volatile ("bl timer_wait" : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x5) :: '
        '"lr", "cc", "memory", "r12"); delay = x0; r1 = x1; r3 = x5; r12 = 0; }\n')


def compacted(body):
    tree = cparse.parse(HEAD + "extern u32 g(u32);\nu32 f(u32 delay, u32 r1, u32 arg)\n{\n    u32 r3, r12;\n" + body +
                        "    return delay + r1 + r3 + r12;\n}\n")
    fn = tree.function("f")
    changed = bound.compact(fn)
    return changed, cparse.unparse(fn, notes=False, style="readable")


def test_the_block_as_the_lifter_writes_it_is_one_statement():
    """IO where a register goes back into the variable it came from, IN where nothing takes it back,
    INOUT where it goes back into another; what followed the write-backs (r12 = 0) follows."""
    changed, out = compacted(CALL)
    assert changed
    assert "BOUND(timer_wait, IO(r0, delay), IO(r1, r1), IN(r2, 5), INOUT(r5, arg, r3));" in out, out
    assert "r12 = 0;" in out and "__asm__" not in out and "register" not in out, out


def test_r12_bound_and_inside_an_arm():
    """r12 among the registers: BOUND_R12 (the asm binds it instead of clobbering it); a block that is an
    if's arm becomes the statement there."""
    text = ('    if (delay != 0) { register u32 x0 __asm__("r0") = delay, x12 __asm__("r12") = arg; '
            '__asm__ volatile ("bl timer_wait" : "+r"(x0), "+r"(x12) :: "lr", "cc", "memory"); delay = x0; r12 = x12; }\n')
    changed, out = compacted(text)
    assert changed and "BOUND_R12(timer_wait, IO(r0, delay), INOUT(r12, arg, r12));" in out, out


@pytest.mark.parametrize("change", [
    ('r3 = x5;', 'r3 = (u32)x5;'),                          # (a cast taking it back: a typed variable)
    ('"bl timer_wait"', '"bl timer_wait\\n\\tmrs %4, apsr"'),  # (the flags read behind it)
    ('"lr", "cc", "memory", "r12"', '"lr", "cc", "memory", "r12", "s0"'),     # (a hard-float callee)
    ('r12 = 0; }', 'r12 = x1; }'),                          # (a variable of the block read later)
])
def test_other_blocks_stay(change):
    changed, out = compacted(CALL.replace(*change))
    assert not changed and "__asm__" in out, out


@pytest.mark.compiles
def test_the_macro_makes_the_same_instructions_as_the_block(tmp_path):
    """The block and the BOUND statement compact writes of it, compiled by gizmo a32's compiler with r5
    kept from the allocator (-ffixed-r5, as dread.build compiles a file that reads a register as left):
    the same instructions."""
    from conftest import gizmo_dir
    from dread.target import Target
    target = Target.load(gizmo_dir("a32"))
    cc = target.compiler.command("cc")
    if not shutil.which(cc[0]):
        pytest.skip("no %s" % cc[0])
    import dread
    include = os.path.join(os.path.dirname(dread.__file__), "include")
    source = ('#include "dread.h"\nu32 f(u32 delay, u32 r1, u32 arg)\n{\n    u32 r3 = 0, r12;\n' + CALL +
              "    return delay + r1 + r3 + r12;\n}\n")
    tree = cparse.parse(source)
    fn = tree.function("f")
    assert bound.compact(fn)
    texts = {"block": source, "macro": cparse.unparse(tree, notes=False, style="readable")}
    assert "BOUND(" in texts["macro"]
    code = {}
    for name, text in texts.items():
        (tmp_path / (name + ".c")).write_text(text)
        obj = tmp_path / (name + ".o")
        r = subprocess.run(cc + target.compiler.cflags + ["-Os", "-ffixed-r5", "-I", include, "-c",
                                                          str(tmp_path / (name + ".c")), "-o", str(obj)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        dump = subprocess.run(target.compiler.command("objdump") + ["-d", str(obj)], capture_output=True, text=True)
        code[name] = [line.split("\t", 2)[-1] for line in dump.stdout.splitlines() if line[:1] == " " and "\t" in line]
    assert code["block"] == code["macro"] and code["block"], code
