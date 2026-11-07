"""dread.lift_arc: what gizmo-arc lifts and why the rest is refused, and assembled functions for
the ARC's own constructs (delay slots, the zero-overhead loop, conditional execution and the flags)
lifted, accepted by the lift check and run against the original. The tests that compile or
assemble need Synopsys' ARC GNU toolchain (arc-elf32-) on PATH; the rest needs none."""
import shutil
import subprocess

import pytest

from conftest import gizmo_dir
from dread import lift, regflow
from dread.program import Program
from dread.target import Target

needs_arc_gcc = pytest.mark.skipif(not shutil.which("arc-elf32-gcc"), reason="needs arc-elf32-gcc")


def lift_gizmo_arc():
    target = Target.load(gizmo_dir("arc"))
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    lifted, refused = {}, {}
    for fn in program.functions:
        try:
            lifted[fn.name] = lifter.lift(fn)
        except lift.Refused as e:
            refused[fn.name] = str(e)
    return target, image, program, lifter, lifted, refused


def test_gizmo_arc_lifts():
    """Of gizmo-arc's functions only the startup (mov sp, limm) and two of libgcc's 64-bit
    multiplication are refused: __muldi3 sets r1 for __umulsidi3 in bl.d's slot, which
    dread.regflow takes for a read behind the call (it does not know delay slots yet). Every name of
    the lifted table lifts."""
    target, image, program, lifter, lifted, refused = lift_gizmo_arc()
    assert refused.pop("_start") == "0x100 changes the stack pointer in a way not modelled: mov sp,0x110000"
    assert "reads r2 behind the call of __umulsidi3" in refused.pop("__muldi3")
    assert "its callers read r2" in refused.pop("__umulsidi3")
    assert refused == {}
    from dread.build import read_names
    table = read_names(image.table("lifted"))
    assert len(table) >= 30 and set(table) <= set(lifted)


def test_gizmo_arc_delay_slots():
    """A delayed branch's condition is taken before its slot runs, the slot's statement comes before
    the branch; a call's slot sets its argument first; a return's slot runs before it returns."""
    lifted = lift_gizmo_arc()[4]
    mul = lifted["__mulsi3"].text      # bne.d -24; add2.c r0,r0,r1
    assert ('dc = !z; { u32 k = c; __asm__ ("" : "+r"(k)); if (k) { { u32 x = r0, y = (r1 << 2), r = x + y; '
            'r0 = r; } } } if (dc) goto L_a5c;') in mul
    umod = lifted["__umodsi3"].text   # mov r7,blink ... j.d [r7]; mov r0,r1: a return through r7
    assert "r0 = r1; return r0;  /* ac0: j.d [r7] */" in umod
    div = lifted["__udivmodsi4"].text  # brhs.d r1,r2,76; mov lp_count,0x20
    assert "dc = (r1 >= r2); lp_count = 0x20u; if (dc) goto L_b14;" in div
    assert "return ((u64)r1 << 32 | r0);  /* b10: j_s.d [blink] */" in div


def test_gizmo_arc_zero_overhead_loop_and_conditions():
    """lpne leaves for LP_END where Z is set; the body's last instruction counts lp_count down and
    goes back to LP_START; conditional instructions are if statements over the flags."""
    lifted = lift_gizmo_arc()[4]
    div = lifted["__udivmodsi4"].text
    assert "if (!!z) goto L_b04;  /* af8: lpne 0xc */" in div
    assert ('{ u32 k = !c; __asm__ ("" : "+r"(k)); if (k) { r0 = r0 - r1; } } __asm__ ("" : "+r"(lp_count)); '
            'if (--lp_count) goto L_afc;') in div
    assert '{ u32 k = z; __asm__ ("" : "+r"(k)); if (k) { r13 = 0xffffffffu; } }  /* 366: mov.eq r13,0xffffffff */' \
        in lifted["parse_uint"].text
    # handle_key's and script_run's switches: tables of byte offsets inside the code
    assert "switch (index) { case 0: goto L_5fa;" in lifted["script_run"].text
    assert "case 10: goto L_956;" in lifted["handle_key"].text
    # an interrupt handler: GCC's interrupt attribute writes its saves and j.f [ilink1]
    irq = lifted["irq_entry"].text
    assert 'void __attribute__((interrupt("ilink1"))) irq_entry(void)' in irq and "return;  /* 1e4: j.f [ilink1] */" in irq


SNIPPETS_S = """
    .text
    .global slotsum, zol, zolcc, conds, callslot, intoslot, use
    .align 4
    .type slotsum, @function
slotsum:                    ; the sum of r1 words at r0 (r1 > 0): the loop's add in bne.d's slot
    mov_s r2,r0
    mov_s r0,0
1:  ld.ab r3,[r2,4]
    sub.f r1,r1,1
    bne.d 1b
    add_s r0,r0,r3
    j_s [blink]
    .align 4
    .type zol, @function
zol:                        ; r0 + r1 * r2 by a zero-overhead loop of r1 rounds
    mov lp_count,r1
    lp 2f
    add r0,r0,r2
2:  j_s [blink]
    .align 4
    .type zolcc, @function
zolcc:                      ; as zol, the loop left out where r1 is 0 (lpne)
    tst_s r1,r1
    mov lp_count,r1
    lpne 2f
    add r0,r0,r2
    xor r0,r0,r1
2:  j_s [blink]
    .align 4
    .type conds, @function
conds:                      ; the flags and conditional execution
    cmp r0,r1
    mov.lt r2,1
    mov.ge r2,2
    add.hi r2,r2,4
    sub.f r3,r0,r1
    sbc r3,r3,r2
    rlc r12,r3
    add.f r0,r0,r1
    adc.c r0,r0,r12
    j_s.d [blink]
    add.v r0,r0,r2
    .align 4
    .type callslot, @function
callslot:                   ; the argument set in bl.d's slot
    push_s blink
    bl.d zol
    mov_s r2,3
    pop_s blink
    j_s [blink]
    .align 4
    .type intoslot, @function
intoslot:                   ; a branch into a delay slot
    breq r0,0,1f
    b.d 2f
1:  add_s r0,r0,1
2:  j_s [blink]
    .align 4
    .type use, @function
use:
    j_s [blink]
"""

ARC_TARGET = """
[target]
name = "arc-snippets"

[arch]
name = "arc"
isa = "arc600"

[memory]
regions = [
    {name = "flash", base = 0x0, size = 0x80000, kind = "rom"},
    {name = "ram", base = 0x00100000, size = 0x10000},
    {name = "scratch", base = 0x00200000, size = 0x20000},
]
io = [[0x80000000, 0x80010000]]
stack = {top = 0x00110000, size = 0x2000}
sentinel = 0x0fff0000
scratch = {base = 0x00200000, size = 0x20000}

[image.main]
original = "original/t.bin"
base = 0x0
limit = 0x20000

[compiler]
cross = "arc-elf32-"
cflags = ["-mcpu=arc600", "-Os", "-ffreestanding", "-fno-builtin", "-ffunction-sections"]
"""


@needs_arc_gcc
def test_arc_delay_slots_loops_and_flags_agree(tmp_path):
    """Assembled functions for what ARC has of its own: a loop whose add sits in bne.d's slot, a
    zero-overhead loop (lp and lpne, a body of one and of two instructions), the flags of cmp,
    sub.f and add.f read by conditional moves and adds, sbc, rlc and adc, a return with its slot, a
    call whose argument its slot sets. The lift check accepts them (generated cases, every
    instruction run), and they compute what the original computes on the engine. A branch into a
    delay slot is refused."""
    from dread import elfimport
    from dread.emu import Layout, Machine
    from dread.overlay import load_placement
    (tmp_path / "t.s").write_text(SNIPPETS_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arc-elf32-as", "-mcpu=arc600", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arc-elf32-ld", "-Ttext=0x0", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(ARC_TARGET)
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))
    with pytest.raises(lift.Refused, match="the delay slot of 0x74, is reached from 0x70 too"):
        lifter.lift(p.by_name["intoslot"])
    text = lifter.lift(p.by_name["callslot"]).text
    assert "r2 = 0x3u; r0 = zol(r0, r1, r2, r3, r4, r5, r6, r7);" in text
    names = ["slotsum", "zol", "zolcc", "conds", "callslot"]
    lines = []
    assert sorted(lift.check(t, t.image(), names, out=lines.append, jobs=1)) == sorted(names), "\n".join(lines)
    work = tmp_path / "build" / "lift-check" / "main"
    aliases, _ = load_placement(str(work / "placement.json"))
    rebuilt = (work / "payload.bin").read_bytes()
    calls = [("zol", (5, 3, 7)), ("zolcc", (5, 0, 7)), ("zolcc", (5, 4, 7)),
             ("callslot", (1, 4, 0)), ("conds", (1, 2, 0)), ("conds", (2, 1, 0)), ("conds", (0x7fffffff, 1, 0)),
             ("conds", (0xffffffff, 1, 0)), ("conds", (5, 5, 0))]
    got = []
    for payload, al in ((t.image().payload(), None), (rebuilt, aliases)):
        m = Machine(Layout(t, t.image()), payload, al)
        got.append([m.call(p.by_name[name].entry, *args, max_instructions=100_000).ret for name, args in calls])
    assert got[0] == got[1], got
    x = 5
    for _ in range(4):
        x = (x + 7) ^ 4
    assert got[0][:4] == [26, 5, x, 13]
