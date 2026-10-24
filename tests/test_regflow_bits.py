"""dread.regflow's bits (regflow 26): a read of bits nobody uses is no read. The layers' rules for which
bits of its operands an instruction's used result bits depend on (bit_demand, store_demand) are checked
against the emulator, instruction by instruction (an operand bit outside what a rule names changes no
used bit of the result, a register the layer does not list as read changes nothing at all); then what
they make of programs of our own: a caller that keeps the upper bits of a register a callee left and
stores none of them reads nothing of it, nor does one that hands it to a callee that does; a second
word that is only a callee's constant is a leftover, a division's remainder a result; a function a
pointer may call has its result read; what a function computes for a callee the analysis does not see
(a call through a register, a variadic function's unnamed arguments) stays read."""
import random
import shutil
import subprocess

import pytest

from dread import regflow
from dread.arch.arm import Arm
from dread.arch.riscv import RiscV
from conftest import RISCV_CROSS, needs_riscv_gcc

needs_arm = pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-objcopy")),
                               reason="needs arm-none-eabi binutils")

M32 = 0xFFFFFFFF
CODE, DATA = 0x10000, 0x20000

# Every rule of the ARM layer, in ARM state (an ARMv7-A core) and in Thumb-2 (a Cortex-M4, for the DSP
# extension's extend-and-add): moves and shifts, the bitwise operations with an immediate and with a
# shifted register, sums, products, the extensions with their rotations, the bit fields, movt; and
# instructions without a rule (by a register, with the carry), which must answer None
A32_LINES = """mov r1, r2, lsl #16; mov r1, r2, lsr #16; mov r1, r2, asr #3; mov r1, r2, ror #8; mvn r1, r2, lsl #3;
mov r1, r2, lsr #32; mov r1, r2, asr #32; mov r1, #0x55; movw r1, #0x1234; movt r1, #0x1234;
lsl r1, r2, r3; rrx r1, r2; bic r1, r1, #0xff; bic r1, r2, #0xff00; orr r1, r1, r0, lsl #24; orr r1, r2, #0xf0;
and r1, r2, #0xff00; and r1, r2, r3, asr #7; eor r1, r2, r3, ror #4; orr r1, r2, r3, lsr #5; bic r1, r2, r3, lsl #9;
add r1, r2, r3, lsr #8; add r1, r2, #0x100; sub r1, r2, #1; rsb r1, r2, r3, lsl #2; adc r1, r2, r3;
mul r1, r2, r3; mla r1, r2, r3, r0; mls r1, r2, r3, r0; uxtb r1, r2; uxth r1, r2, ror #16; sxtb r1, r2, ror #8;
sxth r1, r2; uxtab r1, r2, r3, ror #8; sxtah r1, r2, r3; uxtah r1, r2, r3; sxtab r1, r2, r3, ror #24;
ubfx r1, r2, #4, #8; sbfx r1, r2, #4, #8; sbfx r1, r2, #31, #1; bfi r1, r2, #8, #4; bfc r1, #8, #4;
adds r1, r2, r3; movs r1, r2, lsl #4"""
T32_LINES = """lsls r1, r2, #16; lsrs r1, r1, #8; lsl.w r1, r2, #16; lsls r1, r2; asrs r1, r2, #3; ror r1, r2, #12;
ands r1, r2; bics r1, r2; orrs r1, r2; eors r1, r2; adds r1, r2, r3; adds r1, #4; adds r1, r2, #4; subs r1, r2, #4;
muls r1, r2, r1; mvns r1, r2; movs r1, r2; uxtb r1, r2; uxth r1, r2; sxth r1, r2; sxtb r1, r2;
bic r1, r1, #0xff; orr.w r1, r1, r0, lsl #24; orn r1, r2, #0xff; orn r1, r2, r3, lsr #3; and r1, r2, #0xff00;
add.w r1, r2, r3, lsr #8; addw r1, r2, #0x123; subw r1, r2, #0x123; uxtb.w r1, r2, ror #8; uxtab r1, r2, r3;
sxtah r1, r2, r3, ror #16; ubfx r1, r2, #4, #8; sbfx r1, r2, #3, #12; bfi r1, r2, #8, #4; bfc r1, #0, #16;
movt r1, #0xbeef; rsb.w r1, r2, #0; mla r1, r2, r3, r0; adc.w r1, r2, r3"""
RV_LINES = """andi a1, a2, 0xff; andi a1, a2, -16; ori a1, a2, 0x7f0; xori a1, a2, -1; and a1, a2, a3; or a1, a2, a3;
xor a1, a2, a3; slli a1, a2, 16; srli a1, a2, 8; srai a1, a2, 3; srai a1, a2, 31; add a1, a2, a3; addi a1, a2, 100;
sub a1, a2, a3; mul a1, a2, a3; slt a1, a2, a3; sltu a1, a2, a3; slti a1, a2, 5; sltiu a1, a2, -5;
lui a1, 0x12345; auipc a1, 0x10; sll a1, a2, a3; mulhu a1, a2, a3; c.andi a2, 7; c.slli a2, 3; c.srli a2, 5;
c.srai a2, 9; c.add a2, a3; c.mv a2, a3; c.li a2, 5"""
STORES = {"a32": "strb r1, [r2]; strh r1, [r2, #2]; strb r2, [r2]; str r1, [r2]",
          "t32": "strb r1, [r2]; strh r1, [r2, #2]; strb.w r1, [r2, #-1]; strh r2, [r2]",
          "rv32": "sb a1, 0(a2); sh a1, 2(a2); sb a2, 0(a2); sw a1, 0(a2)"}
WHOLE_STORES = {"strb r2, [r2]", "str r1, [r2]", "strh r2, [r2]", "sb a2, 0(a2)", "sw a1, 0(a2)"}


def _arm_layer(kind):
    return Arm(isa="a32", profile="a") if kind == "a32" else Arm(isa="t32", profile="m", cpu="cortex-m4")


def assemble(tmp_path, kind, text):
    """[(insn, bytes)] of the instructions of text (separated by ;) as the layer decodes them."""
    lines = [x.strip() for x in text.replace("\n", " ").split(";") if x.strip()]
    if kind == "rv32":
        head, layer = ".option norelax\n.text\n", RiscV()
        as_cmd = [RISCV_CROSS + "as", "-march=rv32imc", "-mabi=ilp32"]
        objcopy = RISCV_CROSS + "objcopy"
    else:
        mode = ".arch armv7-a\n.arm\n" if kind == "a32" else ".cpu cortex-m4\n.thumb\n"
        head = ".syntax unified\n" + mode + ".text\n"
        layer, as_cmd, objcopy = _arm_layer(kind), ["arm-none-eabi-as"], "arm-none-eabi-objcopy"
    out = []
    for k, line in enumerate(lines):
        src = tmp_path / ("i%d.s" % k)
        src.write_text(head + "    " + line + "\n")
        obj, raw = tmp_path / ("i%d.o" % k), tmp_path / ("i%d.bin" % k)
        subprocess.run(as_cmd + ["-o", str(obj), str(src)], check=True)
        subprocess.run([objcopy, "-O", "binary", "-j", ".text", str(obj), str(raw)], check=True)
        code = raw.read_bytes()
        insn = layer.decode(code, CODE, "t32" if kind == "t32" else ("rv32" if kind == "rv32" else "a32"))
        assert insn is not None and insn.size == len(code), (line, code.hex())
        out.append((line, insn, code))
    return layer, out


class Core:
    """One instruction in the emulator: run(code, registers) -> the registers after it, and the data page."""

    def __init__(self, layer, kind):
        self.layer, self.kind = layer, kind
        self.uc = layer.new_uc("t32" if kind == "t32" else None)
        self.uc.mem_map(CODE, 0x1000)
        self.uc.mem_map(DATA, 0x1000)
        self.names = ["r%d" % i for i in range(13)] if kind != "rv32" else \
            ["a%d" % i for i in range(8)] + ["t%d" % i for i in range(7)]

    def run(self, code, regs):
        uc, layer = self.uc, self.layer
        uc.mem_write(CODE, bytes(code) + b"\0" * 4)
        uc.mem_write(DATA, b"\0" * 0x100)
        if self.kind != "rv32":
            layer.initial_status(uc)
        for name in self.names:
            uc.reg_write(layer.reg_id(name), regs.get(name, 0) & M32)
        start = CODE | (1 if self.kind == "t32" else 0)
        uc.emu_start(start, CODE + len(code), count=1)
        return {name: uc.reg_read(layer.reg_id(name)) & M32 for name in self.names}, bytes(uc.mem_read(DATA, 0x100))


def random_masks(rng):
    return [M32, 0xFF, 0xFFFF, 0xFF00, 0xFFFF0000, 1, 1 << 31, 0x80, rng.getrandbits(32), rng.getrandbits(32) | 1]


def inputs(core, rng, data_regs=()):
    regs = {name: rng.getrandbits(32) for name in core.names}
    for r in data_regs:                     # (a store's base: inside the data page)
        regs[r] = DATA + 0x10 + rng.randrange(0, 0x40, 4)
    return regs


@pytest.mark.parametrize("kind", [pytest.param("a32", marks=needs_arm), pytest.param("t32", marks=needs_arm),
                                  pytest.param("rv32", marks=needs_riscv_gcc)])
def test_every_bit_rule_holds_in_the_emulator(tmp_path, kind):
    """For each instruction: a register the layer does not list as read changes none of its results
    (reg_effects; capstone 5 leaves uxtab's second source out: the layer's operand_roles adds it), and
    where the layer has a rule (bit_demand), for result bits used, an operand bit outside what the rule
    names changes none of them; the instructions without a rule answer None. The rules are what
    regflow takes for an instruction that only computes registers, so a wrong one would drop a read
    that matters."""
    layer, insns = assemble(tmp_path, kind, {"a32": A32_LINES, "t32": T32_LINES, "rv32": RV_LINES}[kind])
    core, rng = Core(layer, kind), random.Random(26)
    ruled = 0
    for line, insn, code in insns:
        assert layer.register_only(insn), line
        reads, writes = layer.reg_effects(insn)
        outs = [w for w in writes if w in core.names]
        for _ in range(3):                  # the layer's reads: every other register changes nothing
            regs = inputs(core, rng)
            first = core.run(code, regs)[0]
            for name in core.names:
                if name not in reads and name not in outs:
                    got = core.run(code, dict(regs, **{name: regs[name] ^ M32}))[0]
                    assert all(got[w] == first[w] for w in outs), (line, name, "is read but not listed")
        for mask in random_masks(rng):
            out = {w: mask for w in outs}
            need = layer.bit_demand(insn, out)
            if need is None:
                continue
            ruled += 1
            assert set(need) <= set(reads) | {"sp", "pc"}, (line, need, reads)
            for _ in range(3):
                regs = inputs(core, rng)
                first = core.run(code, regs)[0]
                for name in reads:
                    if name not in core.names:
                        continue
                    free = ~need.get(name, 0) & M32
                    if not free:
                        continue
                    for flip in (free, free & rng.getrandbits(32), free & -free):
                        if not flip:
                            continue
                        got = core.run(code, dict(regs, **{name: regs[name] ^ flip}))[0]
                        for w in outs:
                            assert (got[w] ^ first[w]) & out[w] == 0, (line, "%s bits %#x" % (name, flip), w,
                                                                     "%#x" % out[w], need)
    no_rule = {"a32": ("lsl r1, r2, r3", "rrx r1, r2", "adc r1, r2, r3"), "t32": ("lsls r1, r2", "adc.w r1, r2, r3"),
               "rv32": ("sll a1, a2, a3", "mulhu a1, a2, a3")}[kind]
    for line, insn, _ in insns:
        if line in no_rule:
            assert layer.bit_demand(insn, {w: M32 for w in layer.reg_effects(insn)[1]}) is None, line
    assert ruled > 50


@pytest.mark.parametrize("kind", [pytest.param("a32", marks=needs_arm), pytest.param("t32", marks=needs_arm),
                                  pytest.param("rv32", marks=needs_riscv_gcc)])
def test_a_narrow_store_takes_the_bits_it_stores(tmp_path, kind):
    """strb and strh (sb, sh) store the low byte or halfword of their register (store_demand): the bits
    above change no byte of memory; where the stored register is also the address, every bit is (None),
    as for a word's store."""
    layer, insns = assemble(tmp_path, kind, STORES[kind])
    core, rng = Core(layer, kind), random.Random(7)
    base = "r2" if kind != "rv32" else "a2"
    for line, insn, code in insns:
        got = layer.store_demand(insn)
        reads = layer.reg_effects(insn)[0]
        if line in WHOLE_STORES:            # (a word, or the stored register is the address too)
            assert got is None, line
            continue
        (reg, bits), = got.items()
        assert bits in (0xFF, 0xFFFF) and reg in reads, (line, got)
        for _ in range(4):
            regs = inputs(core, rng, data_regs=(base,))
            first = core.run(code, regs)[1]
            again = core.run(code, dict(regs, **{reg: regs[reg] ^ (~bits & M32)}))[1]
            assert first == again, line


# A callee that leaves a constant in r1 (leaf), a caller that hands it on (mid), callers that keep its
# upper bits through a bic and store a byte of them (top) or hand it to a callee that does (relay, sink),
# one that stores it whole (peek: a reader that matters); a division whose remainder in r1 is a result
# (udivmod, remainder_of); a callback a table holds (cb) and its caller, which computes the callback's
# argument (dispatch); use, the entry, stores what it computes.
LEFT_S = """
    .syntax unified
    .arch armv5te
    .arm
    .text
    .global use, leaf, mid, top, sink, relay, peek, udivmod, remainder_of, cb, dispatch
    .type use, %function
use:
    push    {r4, r5, lr}
    mov     r5, r0
    bl      top
    mov     r0, r5
    bl      relay
    mov     r0, r5
    bl      peek
    mov     r1, r5
    mov     r0, #7
    bl      remainder_of
    add     r4, r0, r5
    mov     r1, r5
    bl      dispatch
    add     r0, r0, r4
    ldr     r1, =0x40000210
    str     r0, [r1]
    pop     {r4, r5, lr}
    bx      lr
    .ltorg
    .size use, . - use
    .type leaf, %function
leaf:                           @ r0 + 1; stores a constant through r1, which it leaves there
    ldr     r2, =0x40000200
    mov     r1, #0x55
    str     r1, [r2]
    add     r0, r0, #1
    bx      lr
    .ltorg
    .size leaf, . - leaf
    .type mid, %function
mid:                            @ leaf's result; what leaf left in r1 passes through
    push    {r4, lr}
    bl      leaf
    pop     {r4, lr}
    bx      lr
    .size mid, . - mid
    .type top, %function
top:                            @ stores the low byte of mid's result at r0, through r1, whose upper bits it keeps
    push    {r4, lr}
    mov     r4, r0
    bl      mid
    and     r0, r0, #0xff
    bic     r1, r1, #0xff       @ the upper 24 bits of what mid left are kept ...
    orr     r1, r1, r0
    strb    r1, [r4]            @ ... and stored nowhere
    pop     {r4, lr}
    bx      lr
    .size top, . - top
    .type sink, %function
sink:                           @ stores r0's low byte through r1, keeping r1's upper bits (stored nowhere)
    ldr     r2, =0x40000204
    and     r0, r0, #0xff
    bic     r1, r1, #0xff
    orr     r1, r1, r0
    strb    r1, [r2]
    bx      lr
    .ltorg
    .size sink, . - sink
    .type relay, %function
relay:                          @ hands mid's result and what mid left in r1 on to sink
    push    {r4, lr}
    bl      mid
    bl      sink
    pop     {r4, lr}
    bx      lr
    .size relay, . - relay
    .type peek, %function
peek:                           @ stores mid's result and all of what mid left in r1
    push    {r4, lr}
    bl      mid
    ldr     r2, =0x40000208
    str     r0, [r2]
    str     r1, [r2, #4]
    pop     {r4, lr}
    bx      lr
    .ltorg
    .size peek, . - peek
    .type udivmod, %function
udivmod:                        @ r1 / r0 in r0, r1 % r0 in r1 (unsigned); r0 = 0: 0 and r1
    mov     r2, r0
    mov     r0, #0
    cmp     r2, #0
    bxeq    lr
    mov     r3, #32
    mov     r12, #0
.Lbit:
    movs    r1, r1, lsl #1
    adc     r12, r12, r12
    cmp     r12, r2
    subhs   r12, r12, r2
    adc     r0, r0, r0
    subs    r3, r3, #1
    bne     .Lbit
    mov     r1, r12
    bx      lr
    .size udivmod, . - udivmod
    .type remainder_of, %function
remainder_of:                   @ the remainder: a caller that reads udivmod's second word
    push    {r4, lr}
    bl      udivmod
    mov     r0, r1
    pop     {r4, lr}
    bx      lr
    .size remainder_of, . - remainder_of
    .type cb, %function
cb:                             @ a callback: dispatch calls it through a register
    add     r0, r0, #1
    bx      lr
    .size cb, . - cb
    .type dispatch, %function
dispatch:                       @ cb(r1 + 4) through the table's pointer
    push    {r4, lr}
    ldr     r3, =table
    ldr     r3, [r3]
    add     r0, r1, #4
    blx     r3
    pop     {r4, lr}
    bx      lr
    .ltorg
    .size dispatch, . - dispatch
    .align 2
table:
    .word   cb
"""
LEFT_NAMES = ["use", "leaf", "mid", "top", "sink", "relay", "peek", "udivmod", "remainder_of", "cb", "dispatch"]


def left_program(tmp_path):
    from test_lift_results import arm_program
    return arm_program(tmp_path, "a32", LEFT_S)


@needs_arm
def test_bits_nobody_uses_are_no_read(tmp_path):
    """top keeps the upper 24 bits of what mid left in r1 through a bic and stores a byte of r1: none of
    them reaches memory, so top reads nothing of mid's r1; relay hands r1 on to sink, which does the same
    (sink reads r0 alone); peek stores r1 whole, the one reader that matters. Until regflow 26 every read
    counted: top, relay and sink read r1 too. leaf's r1 at its return is the constant it stored and mid's
    what leaf left: leftovers (Analysis.leftovers), while udivmod's is the remainder it computes (and its
    caller's r1 where it divides by 0): a result. cb, which a table holds, has its result read by callers
    through a pointer; dispatch, which computes cb's argument for a call through a register, reads r1
    (the analysis sees no reads of a pointer's callee: what a function computes for one stays read)."""
    from dread.program import Program
    t = left_program(tmp_path)
    p = Program(t, t.image())
    flow = regflow.analyse(p)
    assert [caller for caller, _ in flow.sites_of("mid", "r1")] == ["peek"], flow.sites.get("mid")
    assert [caller for caller, _ in flow.sites_of("leaf", "r1")] == ["mid"]
    assert sorted(caller for caller, _ in flow.sites_of("mid", "r0")) == ["peek", "relay", "top"]
    assert flow.read_regs("sink") == ["r0"] and flow.read_regs("relay") == ["r0"]
    assert flow.leftover == ["leaf", "mid"] and flow.is_leftover("mid") and not flow.is_leftover("udivmod")
    assert flow.kept_regs("udivmod") == ["r1"] and flow.why("udivmod", "r1").startswith(
        "read after the call by remainder_of")
    assert "r0" in flow.kept_regs("cb") and flow.why("cb", "r0").startswith(
        "read after the call by %s (its result)" % regflow.POINTER_CALLERS)
    assert flow.read_regs("dispatch") == ["r1"]
    for name in ("top", "relay", "peek", "sink", "use"):    # (no caller reads their result)
        assert "r0" not in flow.kept_regs(name), name
    assert "r0" in flow.kept_regs("remainder_of") and "r0" in flow.kept_regs("dispatch")
    # computed: r0 written for the return alone (leaf's r0 + 1, remainder_of's move, cb's sum); not sink's
    # and top's (a byte of it stored), nor udivmod's (its quotient is read in its own loop)
    assert {"leaf", "remainder_of", "cb"} <= set(flow.computed), flow.computed
    assert not {"sink", "top", "udivmod", "relay"} & set(flow.computed), flow.computed


def test_computed_results_are_the_ones_a_source_returns():
    """regflow's computed on gizmo-m3: the functions whose r0 at a return their own code wrote for the
    return alone are the ones whose source returns a value nobody reads (strlen, memcmp, fmt_dec, ...);
    not timer_irq, whose tail call hands ring_put the address it loads into r0 (an argument, not a
    result: a first version took it for one), nor timer_start, delay, gpio_set or handle_key, whose
    source returns nothing."""
    from conftest import gizmo_dir
    from dread.program import Program
    from dread.target import Target
    t = Target.load(gizmo_dir("m3"))
    flow = regflow.load(t, t.image(), Program(t, t.image()))
    assert {"strlen", "memcmp", "fmt_dec", "mul64", "udiv_var"} <= set(flow.computed), flow.computed
    assert not {"timer_irq", "timer_start", "delay", "gpio_set", "handle_key"} & set(flow.computed), flow.computed


@needs_arm
def test_the_bits_a_read_uses_and_one_functions_facts(tmp_path):
    """regflow 27 keeps the bits a read uses where it uses fewer than all of a register: sink stores r0's
    low byte alone (r1's low byte is r0's, its upper bits stored nowhere), so it reads 0xff of r0 and
    nothing of r1. RegFlow.facts (dread regflow --json, dread gui's registers view): mid's callers by call
    site with the registers each reads after it (peek both words, relay and top the result), mid's own
    call of leaf with the registers it reads after it (both: it hands them on), leftover and computed;
    the cases' facts leave the bits out (no generated case changes for them)."""
    import json
    from dread.program import Program
    t = left_program(tmp_path)
    p = Program(t, t.image())
    flow = regflow.analyse(p)
    assert flow.bits["reads"].get("sink") == {"r0": 0xff}, flow.bits
    facts = flow.facts("sink")
    assert facts["reads"] == ["r0"] and facts["bits"]["reads"] == {"r0": 0xff}
    mid = flow.facts("mid")
    by = {c["caller"]: [r["register"] for r in c["registers"]] for c in mid["callers"]}
    assert by == {"peek": ["r0", "r1"], "relay": ["r0"], "top": ["r0"]}, mid["callers"]
    assert [c["callee"] for c in mid["calls"]] == ["leaf"] and [r["register"] for r in mid["calls"][0]["registers"]] \
        == ["r0", "r1"]
    assert mid["leftover"] is True and flow.facts("udivmod")["leftover"] is False
    assert flow.facts("leaf")["computed"] is True and flow.facts("sink")["computed"] is False
    assert "bits" not in flow.case_facts() and "bits" in flow.to_json()
    again = regflow.RegFlow.from_json(json.loads(json.dumps(flow.to_json())))
    assert again.facts("mid") == mid and again.bits == json.loads(json.dumps(flow.bits))
    out = tmp_path / "flow.json"
    assert regflow.main(["--target", t.root, "--json", str(out), "mid", "sink"]) == 0
    got = json.loads(out.read_text())
    assert got["version"] == regflow.VERSION and set(got["functions"]) == {"mid", "sink"}
    assert got["functions"]["sink"]["bits"]["reads"] == {"r0": 255} and got["result"] == "r0"
    assert regflow.main(["--target", t.root, "--json", str(out), "no_such"]) == 2
