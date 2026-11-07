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


# --- save and restore "millicode" helpers (dread.lift_arc._frame_helper) ---

MILLI_C = r"""
typedef unsigned int u32;
u32 ext(u32 x) { return x * 2654435761u + 1u; }
u32 mix(u32 a, u32 b, u32 c, u32 d, u32 e, u32 f, u32 g, u32 h)
{
    u32 s = ext(a) ^ b;
    s = ext(s) ^ c; s = ext(s) ^ d; s = ext(s) ^ e;
    s = ext(s) ^ f; s = ext(s) ^ g; s = ext(s) ^ h;
    return s + a + b + c + d + e + f + g + h;
}
"""
MILLI_LD = """ENTRY(mix)
SECTIONS { . = 0x1000; .text : { *(.text .text.*) } . = ALIGN(4); .data : { *(.data*) }
.bss : { *(.bss* COMMON) } /DISCARD/ : { *(.comment) *(.note*) } }
"""
# the target compiles the lifted C without -mmillicode (a reconstruction's own flags): its C saves
# the registers its own way, calling no millicode (foreign_calls would drop one that called a
# different variant than the original)
MILLI_TOML = ARC_TARGET.replace('name = "arc-snippets"', 'name = "arc-milli"')


@needs_arc_gcc
def test_arc_save_restore_millicode_is_lifted(tmp_path):
    """A function GCC compiled with -mmillicode: it saves the callee-saved registers it uses by a
    call to __st_r13_to_rNN (stores into the frame it reserved) and returns by a tail-jump to
    __ld_r13_to_rNN_ret (restores them, unwinds the frame, returns through blink). The lifter reads
    those helpers' code (dread.lift_arc._frame_helper), treats the save call as the omitted saves and
    the tail-jump as the epilogue; the lift check accepts mix and it agrees with the original on the
    engine. The lifted C is compiled without -mmillicode (so it calls no millicode of its own)."""
    from dread.emu import Layout, Machine
    (tmp_path / "m.c").write_text(MILLI_C)
    (tmp_path / "t.ld").write_text(MILLI_LD)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(MILLI_TOML)
    elf = str(tmp_path / "original" / "t.elf")
    subprocess.run(["arc-elf32-gcc", "-mcpu=arc600", "-Os", "-mmillicode", "-ffreestanding", "-fno-builtin",
                    "-nostdlib", "-T", str(tmp_path / "t.ld"), "-Wl,--no-warn-rwx-segments", "-e", "mix",
                    "-o", elf, str(tmp_path / "m.c"), "-lgcc"], check=True)
    # the original really uses the save/restore helpers
    dis = subprocess.run(["arc-elf32-objdump", "-d", elf], capture_output=True, text=True).stdout
    assert "__st_r13_to_r" in dis and "__ld_r13_to_r" in dis and "_ret" in dis
    from dread import elfimport
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))
    helper = next(fn for fn in p.functions if fn.name.startswith("__st_r13_to_r"))
    assert lifter._frame_helper(helper.entry)[0] == "save"
    helper = next(fn for fn in p.functions if fn.name.startswith("__ld_r13_to_r"))
    assert lifter._frame_helper(helper.entry)[0] == "restore"
    lines = []
    assert "mix" in lift.check(t, t.image(), ["mix"], out=lines.append, jobs=1), "\n".join(lines)
    work = tmp_path / "build" / "lift-check" / "main"
    from dread.overlay import load_placement
    aliases, _ = load_placement(str(work / "placement.json"))
    rebuilt = (work / "payload.bin").read_bytes()
    args = [(1, 2, 3, 4, 5, 6, 7, 8), (0, 0, 0, 0, 0, 0, 0, 0), (0xffffffff, 1, 2, 3, 4, 5, 6, 7),
            (0x80000000, 0x7fffffff, 9, 10, 11, 12, 13, 14)]
    for a in args:
        orig = Machine(Layout(t, t.image()), t.image().payload(), None).call(p.by_name["mix"].entry, *a).ret
        reb = Machine(Layout(t, t.image()), rebuilt, aliases).call(p.by_name["mix"].entry, *a).ret
        assert orig == reb, (a, orig, reb)


PUSH_HELPERS_S = """
    .text
    .global pusher, popper, worker, use
    .align 4
    .type pusher, @function
pusher:                     ; a save helper that pushes r13, r14, r15 (st.aw), as hand-written code does
    st.aw r15,[sp,-4]
    st.aw r14,[sp,-4]
    j.d [blink]
    st.aw r13,[sp,-4]
    .align 4
    .type popper, @function
popper:                     ; the matching restore helper: pop r13-r15 and blink, return
    mov_s r12,0x4
    mov_s r13,0xc
    ld r15,[sp,0x8]
    ld r14,[sp,0x4]
    ld.ab r13,[sp,r13]
    ld.ab blink,[sp,r12]
    j_s [blink]
    .align 4
    .type worker, @function
worker:                     ; uses r13-r15 across calls to use, so it saves the caller's (bl.d's slot
    push_s blink            ; sets a scratch register, r12, not one being saved)
    bl.d pusher
    mov_s r12,r0
    mov_s r13,r1
    mov_s r14,r2
    mov_s r0,r12
    bl use
    mov_s r15,r0
    mov_s r0,r13
    bl use
    add_s r15,r15,r0
    mov_s r0,r14
    bl use
    add_s r0,r0,r15
    b popper
    .align 4
    .type use, @function
use:                        ; a leaf the lifter can translate (r0 = r0*3 + 1)
    asl_s r1,r0,1
    add_s r0,r0,r1
    add_s r0,r0,1
    j_s [blink]
"""


@needs_arc_gcc
def test_arc_push_style_save_restore_is_lifted(tmp_path):
    """Hand-written save/restore helpers that push (st.aw) and pop (ld.ab), as firmware not compiled
    with GCC's millicode has: a save helper reached by bl.d, a restore helper tail-jumped to that
    unwinds and returns. worker saves r13-r15, uses them across calls, and returns through the
    restore helper; the lift check accepts it and it agrees with the original on the engine."""
    from dread.emu import Layout, Machine
    from dread import elfimport
    from dread.overlay import load_placement
    (tmp_path / "t.s").write_text(PUSH_HELPERS_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arc-elf32-as", "-mcpu=arc600", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arc-elf32-ld", "-Ttext=0x0", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(ARC_TARGET.replace('name = "arc-snippets"', 'name = "arc-push"'))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))
    assert lifter._frame_helper(p.by_name["pusher"].entry) == ("save", 12)
    assert lifter._frame_helper(p.by_name["popper"].entry)[0] == "restore"
    lines = []
    assert "worker" in lift.check(t, t.image(), ["worker", "use"], out=lines.append, jobs=1), "\n".join(lines)
    work = tmp_path / "build" / "lift-check" / "main"
    aliases, _ = load_placement(str(work / "placement.json"))
    rebuilt = (work / "payload.bin").read_bytes()
    for a in [(5, 7, 9), (0, 0, 0), (0xffffffff, 2, 3), (100, 200, 300)]:
        orig = Machine(Layout(t, t.image()), t.image().payload(), None).call(p.by_name["worker"].entry, *a).ret
        reb = Machine(Layout(t, t.image()), rebuilt, aliases).call(p.by_name["worker"].entry, *a).ret
        assert orig == reb, (a, orig, reb)


STATUS_S = """
    .text
    .global crit, use
    .align 4
    .type crit, @function
crit:                       ; a < b ? a + 1 : a - 1, in an interrupt-atomic section (lr/flag STATUS32)
    lr r12,[status32]       ; save STATUS32 (flags and the interrupt-enable bits) in a scratch register
    bic r11,r12,2           ; clear E1 (disable level-1 interrupts)
    flag r11
    cmp r0,r1
    add.lt r0,r0,1
    sub.ge r0,r0,1
    flag r12                ; restore STATUS32 (re-enable interrupts, restore the entry flags)
    j_s [blink]
    .align 4
    .type use, @function
use:
    j_s [blink]
"""


@needs_arc_gcc
def test_arc_lr_flag_status32_is_lifted(tmp_path):
    """An interrupt-atomic critical section: lr saves STATUS32, flag clears its interrupt-enable bit
    and later restores it, with a conditional computation (add.lt/sub.ge) on the flags in between.
    The lifter models the flags as its z/n/c/v and STATUS32's other low bits as a variable (0 at a
    case's entry, interrupts inert); the lift check accepts crit and it agrees with the original on
    the engine."""
    from dread.emu import Layout, Machine
    from dread import elfimport
    from dread.overlay import load_placement
    (tmp_path / "t.s").write_text(STATUS_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arc-elf32-as", "-mcpu=arc600", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arc-elf32-ld", "-Ttext=0x0", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(ARC_TARGET.replace('name = "arc-snippets"', 'name = "arc-status"'))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lines = []
    assert "crit" in lift.check(t, t.image(), ["crit"], out=lines.append, jobs=1), "\n".join(lines)
    work = tmp_path / "build" / "lift-check" / "main"
    aliases, _ = load_placement(str(work / "placement.json"))
    rebuilt = (work / "payload.bin").read_bytes()
    for a in [(5, 7, 0), (7, 5, 0), (0, 0, 0), (0xffffffff, 1, 0), (0x7fffffff, 0x80000000, 0)]:
        orig = Machine(Layout(t, t.image()), t.image().payload(), None).call(p.by_name["crit"].entry, *a).ret
        reb = Machine(Layout(t, t.image()), rebuilt, aliases).call(p.by_name["crit"].entry, *a).ret
        assert orig == reb, (a, orig, reb)


PASSTHROUGH_S = """
    .text
    .global savf, caller, use
    .align 4
    .type savf, @function
savf:                       ; r0 * 3 in an interrupt-atomic section: it saves and restores STATUS32,
    lr r12,[status32]       ; so it neither reads nor leaves its caller's flags (a pass-through)
    bic r11,r12,2
    flag r11
    asl_s r1,r0,1
    add_s r0,r0,r1
    flag r12
    j_s [blink]
    .align 4
    .type caller, @function
caller:                     ; calls savf; it is not bound to set flags for it
    push_s blink
    bl savf
    add_s r0,r0,1
    pop_s blink
    j_s [blink]
    .align 4
    .type use, @function
use:
    asl_s r1,r0,1
    add_s r0,r0,r1
    j_s [blink]
"""


@needs_arc_gcc
def test_arc_flags_saved_and_restored_are_pass_through(tmp_path):
    """A function that saves STATUS32 with lr and restores it with flag (an interrupt-atomic section)
    neither reads nor leaves its caller's flags: dread.regflow pairs the lr and the flag as a neutral
    save (the layer's flag_save_pairs), so savf reads only r0, and its caller is not bound to set the
    flags. Both lift and agree with the original on the engine."""
    from dread.emu import Layout, Machine
    from dread import elfimport
    from dread.overlay import load_placement
    (tmp_path / "t.s").write_text(PASSTHROUGH_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arc-elf32-as", "-mcpu=arc600", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arc-elf32-ld", "-Ttext=0x0", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(ARC_TARGET.replace('name = "arc-snippets"', 'name = "arc-pass"'))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    flow = regflow.analyse(p)
    assert "flags" not in flow.read_regs("savf"), sorted(flow.read_regs("savf"))
    assert "flags" not in flow.kept_regs("savf")
    lines = []
    acc = lift.check(t, t.image(), ["savf", "caller", "use"], out=lines.append, jobs=1)
    assert {"savf", "caller"} <= set(acc), "\n".join(lines)
    work = tmp_path / "build" / "lift-check" / "main"
    aliases, _ = load_placement(str(work / "placement.json"))
    rebuilt = (work / "payload.bin").read_bytes()
    for name in ("savf", "caller"):
        for a in [(5, 0, 0), (0, 0, 0), (0xffffffff, 0, 0), (123456, 0, 0)]:
            orig = Machine(Layout(t, t.image()), t.image().payload(), None).call(p.by_name[name].entry, *a).ret
            reb = Machine(Layout(t, t.image()), rebuilt, aliases).call(p.by_name[name].entry, *a).ret
            assert orig == reb, (name, a, orig, reb)


# --- stack arguments: the naked-entry wrapper (dread.lift_arc.lift, render) ---

STACKARGS_C = r"""
typedef unsigned int u32;
u32 leaf(u32 x) { return x * 3u + 1u; }
u32 stackargs(u32 a, u32 b, u32 c, u32 d, u32 e, u32 f, u32 g, u32 h, u32 i, u32 j)
{
    u32 s = leaf(a) ^ b ^ c ^ d ^ e ^ f ^ g ^ h;
    s = leaf(s) ^ i;
    s = leaf(s) ^ j;
    return s + i * 7u + j;
}
"""
STACKARGS_LD = """ENTRY(stackargs)
SECTIONS { . = 0x1000; .text : { *(.text .text.*) } . = ALIGN(4); .data : { *(.data*) }
.bss : { *(.bss* COMMON) } /DISCARD/ : { *(.comment) *(.note*) } }
"""


@needs_arc_gcc
def test_arc_stack_arguments_are_wrapped(tmp_path):
    """A function that reads arguments beyond the eighth (ARC passes eight in registers, the rest on
    the stack above the entry stack pointer): the lifter wraps it, as on ARM and RISC-V. A naked
    entry reserves the original's frame below the entry stack pointer and calls the body with that
    stack pointer as its ninth argument (sp0); the body reads the frame at sp0 - k and the stack
    arguments at sp0 + off. The lift check accepts it (generated cases, every instruction run) and it
    agrees with the original on the engine for hand-chosen inputs."""
    from dread.emu import Layout, Machine
    from dread import elfimport
    from dread.overlay import load_placement
    (tmp_path / "s.c").write_text(STACKARGS_C)
    (tmp_path / "t.ld").write_text(STACKARGS_LD)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(ARC_TARGET.replace('name = "arc-snippets"', 'name = "arc-stackargs"'))
    elf = str(tmp_path / "original" / "t.elf")
    subprocess.run(["arc-elf32-gcc", "-mcpu=arc600", "-Os", "-ffreestanding", "-fno-builtin", "-nostdlib",
                    "-T", str(tmp_path / "t.ld"), "-Wl,--no-warn-rwx-segments", "-e", "stackargs",
                    "-o", elf, str(tmp_path / "s.c"), "-lgcc"], check=True)
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))
    fn = p.by_name["stackargs"]
    assert lifter.stack_args(fn) == 2 and lifter.can_wrap(fn)
    text = lifter.lift(fn).text
    assert "__attribute__((naked)) void stackargs(void)" in text
    assert "u32 stackargs_dread_body(u32 p0" in text and "u32 sp0)" in text
    assert "(sp0 + 0)" in text and "(sp0 + 4)" in text  # the ninth and tenth arguments
    assert "(sp0 - 4)" in text                           # the frame (saved blink)
    lines = []
    assert "stackargs" in lift.check(t, t.image(), ["stackargs", "leaf"], out=lines.append, jobs=1), "\n".join(lines)
    work = tmp_path / "build" / "lift-check" / "main"
    aliases, _ = load_placement(str(work / "placement.json"))
    rebuilt = (work / "payload.bin").read_bytes()
    args = [(1, 2, 3, 4, 5, 6, 7, 8, 9, 10), (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
            (0xffffffff, 1, 2, 3, 4, 5, 6, 7, 0x80000000, 0x7fffffff),
            (11, 22, 33, 44, 55, 66, 77, 88, 99, 123456)]
    for a in args:
        orig = Machine(Layout(t, t.image()), t.image().payload(), None).call(p.by_name["stackargs"].entry, *a).ret
        reb = Machine(Layout(t, t.image()), rebuilt, aliases).call(p.by_name["stackargs"].entry, *a).ret
        assert orig == reb, (a, orig, reb)


RESTORE_FRAG_S = """
    .text
    .global restorefrag, use
    .align 4
    .type restorefrag, @function
restorefrag:                ; a shared restore/epilogue fragment reached by a tail-jump: it pops
    pop_s r14               ; registers it never pushed (reaching above its entry) and returns,
    j_s.d [blink]           ; unwinding its stack pointer below the entry. Not a stack-argument
    pop_s r13               ; reader; the lifter refuses it rather than wrapping it.
    .align 4
    .type use, @function
use:
    asl_s r1,r0,1
    add_s r0,r0,r1
    j_s [blink]
"""


@needs_arc_gcc
def test_arc_restore_fragment_is_not_wrapped(tmp_path):
    """A restore or epilogue fragment entered by a tail-jump reads above its entry stack pointer (the
    registers a caller saved) while unwinding below it. It is not a function that reads stack
    arguments, so the lifter refuses it (the wrapper would reserve a frame it never had); the guard
    is dread.lift_arc.ArcLifter._unwinds_below_entry."""
    from dread import elfimport
    (tmp_path / "t.s").write_text(RESTORE_FRAG_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arc-elf32-as", "-mcpu=arc600", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arc-elf32-ld", "-Ttext=0x0", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(ARC_TARGET.replace('name = "arc-snippets"', 'name = "arc-frag"'))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))
    fn = p.by_name["restorefrag"]
    assert lifter._unwinds_below_entry(fn)
    with pytest.raises(lift.Refused, match="unwinding below it"):
        lifter.lift(fn)
