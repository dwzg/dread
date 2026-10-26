"""dread.varargs: calls of printf-family functions pass the registers their constant formats take.

The programs here are assembly of our own: a printf in newlib's ARM shape (it pushes r0-r3, its save
area) or in GCC's RISC-V shape (it stores a1-a7 into its frame), which adds up the words its format's
conversions take and stores the sum; an assertion handler that hands its line on to printf in the
register it came in (FreeRTOS's vAssertCalled); its callers; a call whose format comes from the caller,
one whose format takes a 64-bit word, and a tail call."""
import os
import re
import shutil

import pytest

from conftest import needs_riscv_gcc
from dread import cparse, lift, readable, regflow, varargs
from dread.program import Program

needs_arm = pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-ld")),
                               reason="needs arm-none-eabi binutils")
needs_gcc = pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")

FORMATS_S = r"""
    .syntax unified
    .arch armv4t
    .arm
    .text
    .global printf, assert_failed, check_a, check_b, say, report, wide, use
    .type printf, %function
printf:                         @ the sum of the words its format's conversions take, stored to 0x40000110
    push    {r0, r1, r2, r3}    @ the save area: the format and its words where a va_list finds them
    push    {r4, r5, lr}
    ldr     r4, [sp, #12]       @ the format, from r0's slot
    add     r5, sp, #16         @ va_start: the word behind the format
    mov     r0, #0
.Lnext:
    ldrb    r1, [r4], #1
    cmp     r1, #0
    beq     .Lend
    cmp     r1, #37             @ '%'
    bne     .Lnext
    ldrb    r1, [r4], #1
    cmp     r1, #37
    beq     .Lnext
    cmp     r1, #0
    beq     .Lend
    ldr     r2, [r5], #4        @ va_arg
    add     r0, r0, r2
    b       .Lnext
.Lend:
    ldr     r1, =0x40000110
    str     r0, [r1]
    pop     {r4, r5, lr}
    add     sp, sp, #16
    bx      lr
    .ltorg
    .size printf, . - printf
    .type assert_failed, %function
assert_failed:                  @ printf("line %d in %s", line, file): the line handed on in r1 as it came;
    push    {r4, lr}            @ then it waits for a debugger to set the flag (FreeRTOS's vAssertCalled)
    mov     r2, r0
    ldr     r0, =fmt_assert
    bl      printf
    ldr     r1, =0x40000200
1:  ldr     r0, [r1]
    cmp     r0, #0
    beq     1b
    pop     {r4, lr}
    bx      lr
    .ltorg
    .size assert_failed, . - assert_failed
    .type check_a, %function
check_a:                        @ assert_failed(file_a, 17) where its argument is zero
    push    {r4, lr}
    cmp     r0, #0
    bne     1f
    ldr     r0, =file_a
    mov     r1, #17
    bl      assert_failed
1:  pop     {r4, lr}
    bx      lr
    .ltorg
    .size check_a, . - check_a
    .type check_b, %function
check_b:                        @ assert_failed(file_a, 23) where its argument is above 9
    push    {r4, lr}
    cmp     r0, #9
    bls     1f
    ldr     r0, =file_a
    mov     r1, #23
    bl      assert_failed
1:  pop     {r4, lr}
    bx      lr
    .ltorg
    .size check_b, . - check_b
    .type say, %function
say:                            @ printf(fmt, ...) with its caller's format: not known
    push    {r4, lr}
    bl      printf
    pop     {r4, lr}
    bx      lr
    .size say, . - say
    .type report, %function
report:                         @ printf("%d", value), a tail call
    mov     r1, r0
    ldr     r0, =fmt_d
    b       printf
    .ltorg
    .size report, . - report
    .type wide, %function
wide:                           @ printf("%lld", value): a 64-bit word, not known
    push    {r4, lr}
    mov     r3, r1
    mov     r2, r0
    ldr     r0, =fmt_wide
    bl      printf
    pop     {r4, lr}
    bx      lr
    .ltorg
    .size wide, . - wide
    .type use, %function
use:
    push    {r4, lr}
    bl      check_a
    mov     r0, #12
    bl      check_b
    ldr     r0, =fmt_d
    mov     r1, #5
    bl      say
    mov     r0, #7
    bl      report
    mov     r0, #1
    mov     r1, #0
    bl      wide
    pop     {r4, lr}
    bx      lr
    .ltorg
    .size use, . - use
    .section .rodata
fmt_assert: .asciz "line %d in %s"
fmt_d:      .asciz "%d"
fmt_wide:   .asciz "%lld"
file_a:     .asciz "a.c"
"""
NAMES = ["printf", "assert_failed", "check_a", "check_b", "say", "report", "wide", "use"]

FORMATS_RV_S = r"""
    .text
    .global printf, assert_failed, check_a, use
    .type printf, @function
printf:                         # the sum of the words its format's conversions take, stored to 0x80000210
    addi    sp, sp, -48
    sw      a1, 20(sp)          # the save area: the words behind the format, where a va_list finds them
    sw      a2, 24(sp)
    sw      a3, 28(sp)
    sw      a4, 32(sp)
    sw      a5, 36(sp)
    sw      a6, 40(sp)
    sw      a7, 44(sp)
    addi    t1, sp, 20          # va_start
    li      t0, 0
    li      t3, 37              # '%'
.Lnext:
    lbu     t2, 0(a0)
    addi    a0, a0, 1
    beqz    t2, .Lend
    bne     t2, t3, .Lnext
    lbu     t2, 0(a0)
    addi    a0, a0, 1
    beq     t2, t3, .Lnext
    beqz    t2, .Lend
    lw      t2, 0(t1)           # va_arg
    addi    t1, t1, 4
    add     t0, t0, t2
    j       .Lnext
.Lend:
    lui     t2, %hi(0x80000210)
    sw      t0, %lo(0x80000210)(t2)
    addi    sp, sp, 48
    ret
    .size printf, . - printf
    .type assert_failed, @function
assert_failed:                  # printf("line %d in %s", line, file): the line handed on in a1 as it came
    addi    sp, sp, -16
    sw      ra, 12(sp)
    mv      a2, a0
    lui     a0, %hi(fmt_assert)
    addi    a0, a0, %lo(fmt_assert)
    call    printf
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size assert_failed, . - assert_failed
    .type check_a, @function
check_a:                        # assert_failed(file_a, 17) where its argument is zero
    bnez    a0, 1f
    addi    sp, sp, -16
    sw      ra, 12(sp)
    lui     a0, %hi(file_a)
    addi    a0, a0, %lo(file_a)
    li      a1, 17
    call    assert_failed
    lw      ra, 12(sp)
    addi    sp, sp, 16
1:  ret
    .size check_a, . - check_a
    .type use, @function
use:
    addi    sp, sp, -16
    sw      ra, 12(sp)
    call    check_a
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size use, . - use
    .section .rodata
fmt_assert: .asciz "line %d in %s"
file_a:     .asciz "a.c"
"""


@pytest.mark.parametrize("fmt, words", [
    (b"", 0), (b"plain text\r\n", 0), (b"%d", 1), (b"line %d in %s", 2), (b"100%%", 0), (b"%%%d%%", 1),
    (b"%5.2x|%-08p|%c", 3), (b"%*d", 2), (b"%.*s", 2), (b"%*.*u", 3), (b"%lu %ld %hhx %zu %td", 5),
    (b"%lld", None), (b"%llu", None), (b"%jd", None), (b"%qd", None), (b"%f", None), (b"%Lf", None),
    (b"%e", None), (b"%1$d", None), (b"%n", None), (b"%m", None), (b"100%", None), (None, None), ("%d", None),
])
def test_the_words_a_format_takes(fmt, words):
    """One word for each conversion of an int, a pointer, a character or a text, one more for a * width
    or precision, none for %%; anything else (a 64-bit or floating-point argument, a positional one, %n,
    an extension, a % at the end, no bytes at all) is not known."""
    assert varargs.consumed(fmt) == words


def test_a_format_is_read_to_its_end_and_not_into_code():
    """text_at reads up to the NUL, in short pieces where its reader stops at a region's end; clipped
    stops short of a span (code, where two images differ) and gives nothing inside one; a format without
    its end within the limit is not known."""
    memory = b"\xff" * 0x10 + b"%d and %s\0" + b"x" * 40 + b"tail%d"
    base = 0x1000

    def read(a, n):
        if not base <= a < base + len(memory):
            raise ValueError("unmapped")
        return memory[a - base:a - base + n]
    assert varargs.text_at(read, base + 0x10) == b"%d and %s"
    assert varargs.text_at(read, base + 0x10 + 3) == b"and %s"
    assert varargs.text_at(read, base + len(memory) - 6) is None            # (runs off the end: no NUL)
    assert varargs.text_at(read, base + 0x1b, limit=8) is None              # (no NUL within 8 bytes)
    assert varargs.text_at(read, "0x1010") is None
    code = [(base + 0x14, base + 0x18)]
    assert varargs.text_at(varargs.clipped(read, code), base + 0x10) is None    # (its end lies behind code)
    assert varargs.text_at(varargs.clipped(read, code), base + 0x15) is None    # (it starts in code)
    assert varargs.text_at(varargs.clipped(read, code), base + 0x18) == b"s"      # (behind the code)
    assert varargs.arity(1, b"%d", 4) == 3 and varargs.arity(0, b"%d %d %d %d", 4) is None
    assert varargs.arity(0, b"%f", 8) is None and varargs.arity(0, None, 4) is None


def test_the_family_and_the_targets_own():
    """The printf family by name, with the index of the format argument; a target's [abi] formats add
    its own and win; one that is no index is refused."""
    from dread.target import Abi, TargetError
    from dread.arch import get as arch_get
    arch = arch_get({"name": "arm", "isa": "t32", "profile": "m"})
    abi = Abi({"formats": {"log_line": 1, "printf": 0}}, arch)

    class T:
        pass
    t = T()
    t.abi = abi
    fam = varargs.family(t)
    assert fam["log_line"] == 1 and fam["printf"] == 0 and fam["snprintf"] == 2 and fam["_sprintf_r"] == 2
    assert varargs.family(T()) == varargs.FAMILY
    for bad in (-1, "1", True, 1.0):
        with pytest.raises((ValueError, TargetError)):
            Abi({"formats": {"log_line": bad}}, arch)


def arm_target(tmp_path, source=FORMATS_S, toml=None):
    from test_lift_results import arm_program
    tmp_path.mkdir(parents=True, exist_ok=True)
    return arm_program(tmp_path, "a32", source, toml=toml)


@needs_arm
def test_regflow_counts_the_registers_a_constant_format_takes(tmp_path):
    """assert_failed's call of printf has a constant format with two conversions, so it uses r0-r2: the
    line in r1, which assert_failed got from its caller, is read (until regflow 28 a register handed on
    to a variadic function untouched was read by nobody, and assert_failed read the file alone). The
    flow lists the variadic functions (printf: its push of r0-r3) and every call of one, with the
    registers it passes and the format's address where the format is known: not for say (its caller's
    format) nor for wide ("%lld": a 64-bit word); report's tail call is known."""
    t = arm_target(tmp_path)
    p = Program(t, t.image())
    flow = regflow.analyse(p)
    assert flow.read_regs("assert_failed") == ["r0", "r1"]
    assert flow.read_regs("printf") == ["r0"] and flow.read_regs("check_a") == ["r0"]
    assert flow.read_regs("report") == ["r0"]
    assert flow.variadic == ["printf"]
    sites = {caller: [(callee, n, f is not None) for _, callee, n, f in calls]
             for caller, calls in flow.formats.items()}
    assert sites == {"assert_failed": [("printf", 3, True)], "report": [("printf", 2, True)],
                     "say": [("printf", None, False)], "wide": [("printf", None, False)]}, flow.formats
    for caller, calls in flow.formats.items():
        for address, _, n, f in calls:
            assert address in p.by_name[caller].insns
            if f is not None:
                assert varargs.image_format(p, f) in (b"line %d in %s", b"%d")
    again = regflow.RegFlow.from_json(flow.to_json())
    assert again.variadic == flow.variadic and again.formats == flow.formats
    assert "variadic" not in flow.case_facts() and "formats" not in flow.case_facts()


def texts(t, names):
    p = Program(t, t.image())
    flow = regflow.analyse(p)
    context = readable.Context(t, t.image(), p, flow)
    lifter = lift.make_lifter(p, flow)
    return {n: readable.transform(n, lifter.lift(p.by_name[n]).text, context=context) for n in names}, context


@needs_arm
def test_readable_c_passes_what_the_format_takes_and_takes_no_phantom_parameters(tmp_path):
    """assert_failed's call of printf passes the format, the line and the file, printf declared variadic,
    and assert_failed takes the two it reads; its callers pass both (before: the line was lost, since
    printf's push of r0-r3 made no wrapper of assert_failed, and regflow said it read the file alone). say
    hands its caller's format on: its call keeps every argument, it takes r0-r3, and so do its calls; the
    64-bit format of wide keeps every argument too; report's tail call passes two. Both gates take every
    function."""
    t = arm_target(tmp_path)
    got, context = texts(t, NAMES[1:])
    assert context.variadic == {"printf", "say", "wide"}
    a = got["assert_failed"]
    assert "void assert_failed(u32 r0, u32 r1)" in a and "extern void printf(u32, ...);" in a, a
    assert re.search(r"printf\(0x[0-9a-f]+u, r1, r0\);", a), a
    for name, line in (("check_a", "0x11u"), ("check_b", "0x17u")):
        c = got[name]
        assert "void %s(u32 r0)" % name in c and "extern void assert_failed(u32, u32);" in c, c
        assert re.search(r"assert_failed\(0x[0-9a-f]+u, %s\);" % line, c), c
    assert "void say(u32 r0, u32 r1, u32 r2, u32 r3)" in got["say"] and "printf(r0, r1, r2, r3);" in got["say"]
    assert "extern void printf(u32, u32, u32, u32);" in got["say"], got["say"]
    assert re.search(r"printf\(0x[0-9a-f]+u, r1, r0, r1\);", got["wide"]), got["wide"]
    assert re.search(r"printf\(0x[0-9a-f]+u, r0\);", got["report"]), got["report"]
    assert "say(0x" in got["use"] and ", 5, 0, 0);" in got["use"], got["use"]
    lines = []
    assert lift.check(t, t.image(), out=lines.append, jobs=2) == NAMES, lines
    assert readable.check(t, t.image(), out=lines.append, jobs=2) == sorted(NAMES), lines


def _calls(stmts):
    for s in stmts:
        for e in readable.tidy._stmt_all_exprs(s):
            for x in readable.tidy.walk_expr(e):
                if isinstance(x, cparse.Call) and isinstance(x.f, cparse.Name):
                    yield x
        for inner in readable.tidy._inner(s):
            yield from _calls([inner])


@needs_gcc
@needs_arm
def test_the_gate_compares_what_the_format_takes(tmp_path, monkeypatch):
    """assert_failed waits for a flag behind its printf, so check_a's cases stub it (a run hung in it), with
    the registers regflow finds it reading: the file and the line (before, the file alone). A written case
    of assert_failed stubs printf, whose stub logs the registers its format takes (dread.autocase
    format_mask; before, what regflow says printf reads: the format alone). A stage that swaps the line and
    the file in assert_failed's call of printf (printf's sum is the same: only the stub's log differs) and
    one that passes check_a's line wrong: both functions stay lifted, the others are readable."""
    import json
    from dread import autocase
    t = arm_target(tmp_path)
    p = Program(t, t.image())
    os.makedirs(t.image().extra_cases, exist_ok=True)
    with open(os.path.join(t.image().extra_cases, "assert_failed.json"), "w") as f:
        json.dump({"cases": [{"args": [p.by_name["use"].entry, 17, 0, 0], "mem": [[0x40000200, 4, 1]], "returns": [],
                              "calls": [[p.by_name["printf"].entry, 1, 0]], "why": "printf stubbed"}]}, f)

    def swap(fn, run=None):
        for call in _calls(fn.body.body):
            if fn.name == "assert_failed" and call.f.id == "printf" and len(call.args) == 3:
                call.args[1], call.args[2] = call.args[2], call.args[1]
            if fn.name == "check_a" and call.f.id == "assert_failed" and len(call.args) == 2:
                call.args[1] = cparse.Num("0x12u")
    lines = []
    assert lift.check(t, t.image(), out=lines.append, jobs=2) == NAMES, lines
    gen = autocase.Generator(t, t.image(), p)
    assert gen.reads_mask("assert_failed") == 0b11 and gen.reads_mask("printf") == 0b1
    stubbed = json.load(open(os.path.join(t.build, "autocases", t.image().name, "check_a.json")))["cases"]
    assert any([p.by_name["assert_failed"].entry, 0b11, 0] in c.get("calls", ()) for c in stubbed), stubbed
    monkeypatch.setattr(readable, "STAGES", readable.STAGES + [("break", swap)])
    accepted = readable.check(t, t.image(), out=lines.append, jobs=1)     # (in this process: the stage)
    assert accepted == sorted(set(NAMES) - {"assert_failed", "check_a"}), lines
    assert any(line.startswith("lifted   assert_failed") and "FAIL" in line for line in lines), lines
    assert any(line.startswith("lifted   check_a") and "FAIL" in line for line in lines), lines


FILL_S = r"""
    .syntax unified
    .arch armv4t
    .arm
    .text
    .global printf, takes_buf, print_then_fill, use
    .type printf, %function
printf:                         @ stubbed in the case below; its code matters not
    push    {r0, r1, r2, r3}
    add     sp, sp, #16
    bx      lr
    .size printf, . - printf
    .type takes_buf, %function
takes_buf:
    bx      lr
    .size takes_buf, . - takes_buf
    .type print_then_fill, %function
print_then_fill:                @ printf("%d", x), then a buffer on the stack to takes_buf; stores its word
    push    {r4, lr}
    sub     sp, sp, #16
    mov     r1, r0
    ldr     r0, =fmt_d
    bl      printf
    mov     r0, sp
    bl      takes_buf
    ldr     r0, [sp]
    ldr     r1, =0x40000120
    str     r0, [r1]
    add     sp, sp, #16
    pop     {r4, lr}
    bx      lr
    .ltorg
    .size print_then_fill, . - print_then_fill
    .type use, %function
use:
    push    {r4, lr}
    bl      print_then_fill
    pop     {r4, lr}
    bx      lr
    .size use, . - use
    .section .rodata
fmt_d:      .asciz "%d"
"""


@needs_arm
def test_a_formats_registers_change_no_fill(tmp_path):
    """A stub of printf logs the word its "%d" takes (format_mask), and only logs it: the bytes the next
    stub writes behind a pointer to the caller's buffer are seeded by the stub arguments the case's own
    masks log, as before, so a case cached before the formats were logged runs as it was made. Counted
    among them, the format's register moved the seed of every later fill (2026-10-26: a cached case of
    FreeRTOS's GCC -O0 prvCoreATask then overwrote its loop counter and faulted in the original)."""
    from dread import autocase
    from dread.emu import EmuError
    t = arm_target(tmp_path, FILL_S)
    p = Program(t, t.image())
    entry = p.by_name["print_then_fill"].entry
    case = {"args": [7, 0, 0, 0], "mem": [], "returns": [], "frame": 24,
            "calls": [[p.by_name["printf"].entry, 1, 0], [p.by_name["takes_buf"].entry, 1, 0]]}
    runs = []
    for logged in (True, False):
        g = autocase.Generator(t, t.image(), p)
        if not logged:
            g._formats = ({}, g.formats()[1])
        m = g.machine()
        m.plan = []
        g.apply(m, case)
        try:
            m.call(entry, *case["args"], max_instructions=20000)
        except EmuError:
            pass
        runs.append((m.read(0x40000120, 4), list(m.calls)))
    (word, log), (word_before, log_before) = runs
    assert word == word_before and word != b"\0\0\0\0", runs
    fmt = t.image().base + t.image().payload().index(b"%d\0")
    assert ("call 0x%x" % p.by_name["printf"].entry, fmt, 7) in log, log
    assert ("call 0x%x" % p.by_name["printf"].entry, fmt) in log_before, log_before


@needs_arm
def test_a_call_that_ends_the_run_logs_what_the_callee_reads(tmp_path):
    """A case of check_a that ends at its call of assert_failed (stops: a callee a run hung in, as the
    generator makes them) logs the call with the registers assert_failed reads, the file and the line;
    one of assert_failed that ends at printf logs the format and the two words it takes. Until 2026-10-26
    a stop logged the call alone, and in a case that ended there the arguments were compared by nothing
    (a caller that passed the line wrong agreed)."""
    from dread import autocase
    from dread.emu import EmuError
    t = arm_target(tmp_path)
    p = Program(t, t.image())
    g = autocase.Generator(t, t.image(), p)
    image = t.image()
    file_a = image.base + image.payload().index(b"a.c\0")
    fmt = image.base + image.payload().index(b"line %d in %s\0")
    for caller, args, callee, logged in (("check_a", [0, 0, 0, 0], "assert_failed", (file_a, 17)),
                                         ("assert_failed", [file_a, 17, 0, 0], "printf", (fmt, 17, file_a))):
        stop = p.by_name[callee].entry
        case = {"args": args, "mem": [], "returns": [], "calls": [], "hang": True, "limit": 20000, "stops": [stop]}
        m = g.machine()
        m.plan = []
        g.apply(m, case)
        try:
            m.call(p.by_name[caller].entry, *args, max_instructions=20000)
        except EmuError:
            pass
        assert m.stopped_at == "never returns", (caller, m.calls)
        assert ("call 0x%x, never returns" % stop,) + logged in m.calls, (caller, m.calls)


@needs_arm
def test_a_targets_own_function_that_takes_a_format(tmp_path):
    """The same program with printf named log_line: no wrapper's format is known without the target's
    [abi] formats (assert_failed reads the file alone, as before), with formats = {log_line = 0} it is."""
    from test_lift_results import A_TOML
    source = FORMATS_S.replace("printf", "log_line")
    t = arm_target(tmp_path / "plain", source, A_TOML)
    flow = regflow.analyse(Program(t, t.image()))
    assert flow.read_regs("assert_failed") == ["r0"] and flow.variadic == ["log_line"]
    assert all(n is None for calls in flow.formats.values() for _, _, n, _ in calls), flow.formats
    t = arm_target(tmp_path / "own", source, A_TOML + "\n[abi]\nformats = {log_line = 0}\n")
    flow = regflow.analyse(Program(t, t.image()))
    assert flow.read_regs("assert_failed") == ["r0", "r1"]
    got, _ = texts(t, ["assert_failed", "check_a"])
    assert "extern void log_line(u32, ...);" in got["assert_failed"], got["assert_failed"]
    assert "void check_a(u32 r0)" in got["check_a"], got["check_a"]


@needs_riscv_gcc
def test_risc_v_callers_of_an_assertion_handler_take_no_phantom_parameters(tmp_path):
    """On RISC-V, GCC's printf stores a1-a7 into its frame (regflow's variadic save area): every call of
    assert_failed kept all eight arguments, and check_a passed its own a2-a7 on and took them as
    parameters. With the format known, assert_failed reads a0 and a1 and takes them, check_a takes its
    a0 alone, and both gates take every function."""
    from test_results import riscv_program
    t = riscv_program(tmp_path, FORMATS_RV_S, "use")
    flow = regflow.analyse(Program(t, t.image()))
    assert flow.read_regs("assert_failed") == ["a0", "a1"] and flow.variadic == ["printf"]
    names = ["printf", "assert_failed", "check_a", "use"]
    got, _ = texts(t, names[1:])
    assert "void assert_failed(u32 a0, u32 a1)" in got["assert_failed"], got["assert_failed"]
    assert re.search(r"printf\(0x[0-9a-f]+u, a1, a0\);", got["assert_failed"]), got["assert_failed"]
    assert "void check_a(u32 a0)" in got["check_a"], got["check_a"]
    assert re.search(r"assert_failed\(0x[0-9a-f]+u, 0x11u\);", got["check_a"]), got["check_a"]
    lines = []
    assert sorted(lift.check(t, t.image(), out=lines.append, jobs=2)) == sorted(names), lines
    assert sorted(readable.check(t, t.image(), out=lines.append, jobs=2)) == sorted(names), lines

