"""The MSP430 layer: its decoder against LLVM's disassembler (random encodings of every format,
addressing mode, register and width, with random extension words: the text and the length must be
llvm-mc's), the lengths from the first word alone, flow and register effects, and gizmo-msp430's code
against llvm-objdump."""
import os
import random
import re
import shutil
import struct
import subprocess

import pytest

from dread.arch.msp430 import Msp430, decode_fields, length_of, text_of
from conftest import gizmo_dir

needs_llvm = pytest.mark.skipif(not shutil.which("llvm-mc"), reason="needs llvm-mc")
R = random.Random(11)


def random_instruction(address):
    """Bytes of one random MSP430 instruction (first word, then extension words)."""
    kind = R.choices(["double", "single", "jump"], [70, 18, 12])[0]
    if kind == "double":
        w = R.randrange(4, 16) << 12 | R.randrange(16) << 8 | R.randrange(2) << 7 | R.randrange(2) << 6 \
            | R.randrange(4) << 4 | R.randrange(16)
    elif kind == "single":
        w = 0x1000 | R.randrange(6) << 7 | R.randrange(2) << 6 | R.randrange(4) << 4 | R.randrange(16)
    else:
        w = 0x2000 | R.randrange(8) << 10 | R.randrange(0x400)
    words = [w] + [R.choice([0, 1, 2, 0x7FFF, 0x8000, 0xFFFF, R.randrange(0x10000)])
                   for _ in range(length_of(w) // 2 - 1)]
    return struct.pack("<%dH" % len(words), *words)


CG_VALUES = (0, 1, 2, 4, 8, 0xFFFF, 0xFF)


def canonical(f):
    """Whether llvm-mc would encode the instruction the same way: an immediate that a constant
    generator gives is written with the generator, not an extension word."""
    for o in (f.src, f.dst):
        if o is not None and o.mode == "imm" and not o.cg and o.value in CG_VALUES:
            return False
    branch = f.op == "call" or (f.op == "mov" and f.dst.mode == "reg" and f.dst.reg == 0)
    if branch and f.src.mode == "imm" and f.src.cg:
        return False            # (a call or br to 0, 1, 2, 4, 8 or -1: llvm-mc writes the target as a word)
    return True


@needs_llvm
def test_the_text_assembles_back_to_the_same_bytes(tmp_path):
    """Random encodings of every format, addressing mode, register and width: the decoder's text,
    assembled by llvm-mc, gives back the bytes (what dread asm needs). llvm-mc refuses a few valid
    forms (no @rN+ source with a memory destination, no push.b, no push X(rN), no @pc source);
    those become raw words in a listing, and they may be no more than a small share."""
    address, cases = 0x8000, []
    while len(cases) < 3000:
        code = random_instruction(address)
        f = decode_fields(code, address)
        if f.op != "unknown" and f.target is None and canonical(f):
            cases.append((address, code, f))
        address += len(code)
    source = tmp_path / "t.s"
    source.write_text("".join("\t%s %s\n" % text_of(f, a) for a, _, f in cases))
    got = subprocess.run(["llvm-mc", "-triple=msp430", "-show-encoding", str(source)], capture_output=True, text=True)
    refused = {int(m.group(1)) - 1 for m in re.finditer(r"t\.s:(\d+):\d+: error", got.stderr)}
    encodings = [bytes(int(b, 16) for b in m.group(1).split(","))
                 for m in re.finditer(r"encoding: \[([0-9a-fx,]+)\]", got.stdout)]
    accepted = [c for k, c in enumerate(cases) if k not in refused]
    assert len(encodings) == len(accepted)
    assert len(refused) < len(cases) // 10, "llvm-mc refuses %d of %d" % (len(refused), len(cases))
    differ = [(text_of(f, a), code.hex(), enc.hex()) for (a, code, f), enc in zip(accepted, encodings) if enc != code]
    assert not differ, differ[:10]


def test_lengths_from_the_first_word():
    assert length_of(0x4C0D) == 2                   # mov r12, r13
    assert length_of(0x403E) == 4                   # mov #N, r14: an immediate word
    assert length_of(0x4392) == 4                   # mov #1, &N (constant generator, absolute destination)
    assert length_of(0x5292) == 6                   # add &N, &N
    assert length_of(0x12B0) == 4                   # call #N
    assert length_of(0x3FFF) == 2                   # jmp


class Insn:
    def __init__(self, address, code):
        f = decode_fields(code, address)
        self.address, self.size, self.fields, self.bytes = address, f.size, f, code[:f.size]
        self.mnemonic, self.op_str = text_of(f, address)


def test_flow_and_effects():
    arch = Msp430()
    call = Insn(0x8000, struct.pack("<HH", 0x12B0, 0x8100))
    assert arch.flow(call) == ("call", 0x8100, True)
    assert arch.flow(Insn(0x8000, struct.pack("<H", 0x4130))) == ("return", None, False)      # ret
    assert arch.flow(Insn(0x8000, struct.pack("<HH", 0x4030, 0x8200))) == ("jump", 0x8200, False)  # br #N
    assert arch.flow(Insn(0x8000, struct.pack("<H", 0x2402))) == ("jump", 0x8006, True)        # jeq $+6
    assert arch.flow(Insn(0x8000, struct.pack("<H", 0x1300))) == ("return", None, False)       # reti
    assert arch.reg_effects(Insn(0x8000, struct.pack("<H", 0x5D0C))) == ({"r12", "r13"}, {"r12", "flags"})
    assert arch.stack_kind(Insn(0x8000, struct.pack("<H", 0x120A))) == "push"                  # push r10
    assert arch.stack_kind(Insn(0x8000, struct.pack("<H", 0x413A))) == "pop"                   # pop r10
    assert arch.branch(0x8000, 0x9000, "msp430") == struct.pack("<HH", 0x4030, 0x9000)


def test_gizmo_msp430_against_llvm_objdump():
    from dread.program import Program
    from dread.target import Target
    if not shutil.which("llvm-objdump"):
        pytest.skip("needs llvm-objdump")
    target = Target.load(gizmo_dir("msp430"))
    program = Program(target)
    out = subprocess.run(["llvm-objdump", "-d", os.path.join(target.root, "original", "gizmo.elf")],
                         capture_output=True, text=True, check=True).stdout
    truth = {}
    for line in out.splitlines():
        m = re.match(r"\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*\t(\S+)(?:\t(.*))?$", line)
        if m:
            truth[int(m.group(1), 16)] = (m.group(3), (m.group(4) or "").strip(), len(m.group(2).split()))
    n = 0
    for fn in program.functions:
        for insn in program.instructions(fn):
            assert insn is not None, fn.name
            assert (insn.mnemonic, insn.op_str, insn.size) == truth[insn.address], (fn.name, hex(insn.address))
            n += 1
    assert n > 1500


def test_import_elf_reads_untyped_entries_and_free_ranges():
    """libgcc's MSP430 shift routines and shared epilogues are assembly without .type: their global
    labels are entries, each running to the next; the gap between the code and the vectors is free."""
    from dread.elf import Elf
    from dread.elfimport import entry_labels, free_ranges, tables
    elf = Elf(os.path.join(gizmo_dir("msp430"), "original", "gizmo.elf"))
    labels = {name: (lo, hi) for lo, hi, name in entry_labels(elf)}
    assert labels["__mspabi_func_epilog_7"][1] - labels["__mspabi_func_epilog_7"][0] == 2
    assert labels["__mspabi_func_epilog_1"][1] - labels["__mspabi_func_epilog_1"][0] == 4    # pop r10; ret
    functions, code, _ = tables(elf, isa="msp430")
    names = {name for _, name, _ in functions}
    assert {"__mspabi_func_epilog_3", "__mspabi_srli", "__mspabi_divllu", "memcpy"} <= names
    assert any(lo <= labels["__mspabi_srli"][0] < hi for lo, hi, _ in code)
    assert free_ranges(elf, 0x8000) == [(0x9710, 0xFFE0)]


@pytest.mark.skipif(not shutil.which("msp430-elf-gcc"), reason="needs TI's MSP430 GCC")
def test_gizmo_msp430_rewritten_functions_agree(tmp_path):
    """The rewritten C over the original on the MSP430 engine: moved functions land in the image's
    free flash, and every function agrees with generated cases covering every instruction."""
    from dread import build, runner
    from dread.target import Target
    from test_runner import src_copy
    target = Target.load(gizmo_dir("msp430"))
    payload = build.build(target, target.image(), src=str(src_copy(tmp_path, "msp430")), out=str(tmp_path / "recon"),
                          quiet=True)
    with open(tmp_path / "recon" / "report.tsv") as f:
        report = f.read()
    assert "into the image's free space" in report
    suite = runner.prepare(target, target.image(), payload)
    lines = []
    failed, uncovered, _ = runner.run(list(suite.cases), out=lines.append, jobs=4)
    text = "\n".join(lines)
    assert failed == 0 and not uncovered, text
    assert "ok   checksum" in text and "ok   fmt_hex" in text


needs_ti_gcc = pytest.mark.skipif(not shutil.which("msp430-elf-gcc"), reason="needs TI's MSP430 GCC")


def lift_gizmo_msp430():
    from dread import lift, regflow
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir("msp430"))
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


def test_a_runtime_helper_keeps_its_abi_result():
    """A runtime helper's result is what the MSP430 EABI says (__mspabi_mpyl: 32 bits in r12:r13),
    whatever the original's own callers read of it: gizmo's parse_uint, rewritten in C, multiplies
    through it and reads r13, which no call of the original does. dread.regflow takes the ABI's
    result as read, so the lifted helper returns all of it and its cases compare it."""
    from dread import lift, regflow
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir("msp430"))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    assert flow.kept_regs("__mspabi_mpyl") == ["r12", "r13"]
    assert "the ABI (its result)" in flow.why("__mspabi_mpyl", "r13")
    assert lift.make_lifter(program, flow)._width("__mspabi_mpyl") == 2


def test_gizmo_msp430_lifts():
    """dread.lift_msp430 on gizmo: of gizmo's own functions it refuses a caller of libgcc's 64-bit
    multiplication (which takes r8-r11 as its caller leaves them) and the startup. libgcc's shift
    routines fall into one another: each falls into the next one's entry as a tail call of it, the
    logical right shifts too, which begin with clrc, a write of C that passes the other flags
    through (with the flags as one unit, the next one read them, and its callers were refused)."""
    target, image, program, lifter, lifted, refused = lift_gizmo_msp430()
    own = {name: why for name, why in refused.items() if not name.startswith("__")}
    assert own == {
        "_start": "0x8000 changes the stack pointer in a way not modelled: mov #2560, r1",
        "mul64": "0x847c calls __mspabi_mpyll, which reads r10, r11, r8, r9 its caller leaves",
    }
    assert "r12 = __mspabi_slli_1(r12, r13, r14, r15);" in lifted["__mspabi_slli_2"].text
    assert "r12 = __mspabi_srli_3(r12, r13, r14, r15);" in lifted["__mspabi_srli_4"].text
    assert len(lifted) == 112
    # a call through a register that holds a function's entry is a call of that function, and the
    # width of its result is what the code behind it reads (uart_getc's: r12 alone)
    assert "r12 = uart_puts(r12, r13, r14, r15);" in lifted["print_status"].text
    assert "r12 = uart_getc(r12, r13, r14, r15);" in lifted["read_line"].text
    # a branch to a shared epilogue is a return; a 32-bit result goes through a union
    assert "return ((dread_w32){ .h = { r12, r13 } }).w;  /* 81ea: br #36442 */" in lifted["checksum"].text
    # a function that returns with reti: GCC's interrupt attribute writes the entry and the return
    text = lifted["irq_entry"].text
    assert "void __attribute__((interrupt)) irq_entry(void)" in text and "return;  /* 8050: reti  */" in text


def test_gizmo_msp430_stack_arguments():
    """Arguments beyond r12-r15 come on the stack, above the return address: the lifted function
    has them as parameters after p12-p15 and reads them through the parameters' addresses, where its
    caller put them (TI's GCC keeps a stack parameter whose address is taken in the slot it came
    in); a caller passes the words of its frame that lie where the callee reads them."""
    target, image, program, lifter, lifted, refused = lift_gizmo_msp430()
    sum6 = lifted["sum6"].text          # u32 sum6(u32 a, ..., u32 f): c to f are eight stack words
    assert "u16 sum6(u16 p12, u16 p13, u16 p14, u16 p15, %s)" % ", ".join("u16 sa%d" % k for k in range(8)) in sum6
    assert "{ s0 = *(volatile u16 *)((u16)&sa2); d0 = s0; r11 = d0; }  /* 83f6: mov 20(r1), r11 */" in sum6
    assert lifted["sum6"].stack_params == 8
    assert "u16 parse_kv(u16 p12, u16 p13, u16 p14, u16 p15, u16 sa0)" in lifted["parse_kv"].text
    # apply_config stores &value at 0(r1), the bottom of its 68 bytes, and calls parse_kv
    apply = lifted["apply_config"].text
    assert "extern u16 parse_kv(u16, u16, u16, u16, u16);" in apply
    assert "r12 = parse_kv(r12, r13, r14, r15, *(volatile u16 *)((u16)(sp0 - 68)));" in apply
    # libgcc's 32-bit division entries tell udivmodsi4 on the stack whether they want the remainder
    assert "x_.w = udivmodsi4(r12, r13, r14, r15, *(volatile u16 *)((u16)(sp0 - 2)));" in lifted["__mspabi_remul"].text
    assert "{ s0 = 0x0u; a0 = (u16)&sa0; e0 = *(volatile u16 *)(a0);" in lifted["udivmodsi4"].text


STACK_ARGUMENT_CODE = """
.text
.macro fn name
.global \\name
.type \\name, @function
\\name:
.endm
fn takes_one
    mov 2(r1), r13
    add r13, r12
    ret
fn high_byte
    push r4
    mov r1, r4
    mov.b 5(r4), r12
    pop r4
    ret
fn passes_on
    br #takes_one
fn calls_one
    push #7
    call #takes_one
    add #2, r1
    ret
fn address_of
    push r10
    mov r1, r15
    add #4, r15
    mov @r15, r12
    pop r10
    ret
fn reads_back
    sub #2, r1
    mov #5, 0(r1)
    call #takes_one
    mov 0(r1), r12
    add #2, r1
    ret
fn tail_with_frame
    push r10
    br #takes_one
fn return_address
    mov 0(r1), r12
    ret
fn wakes_up
    bic #16, 0(r1)
    reti
"""


def msp430_asm_target(tmp_path, code, entry):
    """(target, image, program) of an image made from MSP430 assembly (msp430-elf-gcc, at 0x4000), with
    the lifter fuzzer's memory map and its compiler settings."""
    from dread import elfimport, liftfuzz
    from dread.elf import Elf
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.S").write_text(code)
    (tmp_path / "t.ld").write_text("MEMORY { text (rx) : ORIGIN = 0x4000, LENGTH = 0xbf00 }\n"
                                   "SECTIONS { .text : { *(.text .text.*) } > text }\n")
    elf_path = str(tmp_path / "t.elf")
    subprocess.run(["msp430-elf-gcc", "-mcpu=msp430", "-nostdlib", "-T", str(tmp_path / "t.ld"), "-Wl,-e," + entry,
                    "-o", elf_path, str(tmp_path / "t.S")], check=True)
    (tmp_path / "dread.toml").write_text(liftfuzz.ISAS["msp430"]["toml"].replace(
        'original = "fuzz.bin"', 'original = "t.bin"').replace('entry = "f0"', 'entry = "%s"' % entry) +
        '\n[compiler]\ncross = "msp430-elf-"\nldflags = []\ncflags = ["-mcpu=msp430", "-mhwmult=none", "-Os", '
        '"-ffreestanding", "-fno-builtin", "-ffunction-sections", "-fno-common"]\n')
    target = Target.load(str(tmp_path))
    image = target.image()
    elf = Elf(elf_path)
    elfimport.write_tables(image, *elfimport.tables(elf, isa="msp430"))
    (tmp_path / "t.bin").write_bytes(elf.payload(image.base, image.erased)[0])
    return target, image, Program(target, image)


@needs_ti_gcc
def test_msp430_stack_argument_rules(tmp_path):
    """Hand-written cases of what the lifter does with the stack above a function's return address:
    a stack word read directly, a byte of one through a frame pointer, a tail call that passes its
    own stack word on, a call that pushes one; refused: the address of a stack argument in a
    register, a word read again behind the call it was passed to (the callee may have changed it;
    the C passed a copy), a tail call with a frame, the return address, an interrupt handler's
    saved status register."""
    from dread import lift
    target, image, program = msp430_asm_target(tmp_path, STACK_ARGUMENT_CODE, "takes_one")
    lifter = lift.make_lifter(program)
    lifted, refused = {}, {}
    for fn in program.functions:
        try:
            lifted[fn.name] = lifter.lift(fn)
        except lift.Refused as e:
            refused[fn.name] = str(e)
    assert sorted(lifted) == ["calls_one", "high_byte", "passes_on", "takes_one"], refused
    assert "u16 takes_one(u16 p12, u16 p13, u16 p14, u16 p15, u16 sa0)" in lifted["takes_one"].text
    assert "s0 = *(volatile u16 *)((u16)&sa0); d0 = s0; r13 = d0;" in lifted["takes_one"].text
    assert "s0 = (u16)*(volatile u8 *)((u16)((u16)&sa0 + 1u));" in lifted["high_byte"].text     # 5(r4), r4 = sp + 2
    assert "r12 = takes_one(r12, r13, r14, r15, *(volatile u16 *)((u16)&sa0));" in lifted["passes_on"].text
    assert lifted["passes_on"].stack_params == 1
    assert "r12 = takes_one(r12, r13, r14, r15, *(volatile u16 *)((u16)(sp0 - 2)));" in lifted["calls_one"].text
    assert "takes the address of a stack argument" in refused["address_of"]
    assert re.search(r"0x\w+ reads a stack word that the call at 0x\w+ passed to takes_one", refused["reads_back"])
    assert "jumps to takes_one with 2 bytes of its own on the stack" in refused["tail_with_frame"]
    assert "reads or writes its return address" in refused["return_address"]
    assert "the status register the interrupt saved" in refused["wakes_up"]


@needs_ti_gcc
def test_gizmo_msp430_lifted_c_calls_nothing_of_libgcc(tmp_path):
    """The lifted C compiles, and no object calls what its original does not (GCC would shift in
    libgcc for a flag, a byte swap or two rotations in a row; dread.lift.foreign_calls refuses that
    in a lift check); the shared epilogues GCC's own code jumps to are allowed."""
    from dread import build, lift
    target, image, program, lifter, lifted, refused = lift_gizmo_msp430()
    src = tmp_path / "lifted"
    src.mkdir()
    for name, result in lifted.items():
        (src / (name + ".c")).write_text(result.text)
    objects = build.compile_all(target, image, str(src), str(tmp_path / "obj-lifted"),
                                flags=target.compiler.lifted_cflags)
    assert len(objects) == len(lifted)
    lifter.lifted = lifted
    assert lift.foreign_calls(program, str(tmp_path), sorted(lifted), lifter) == {}
    assert any("dread_rra16(" in r.text for r in lifted.values())


@pytest.mark.slow
@needs_ti_gcc
def test_random_msp430_functions_agree(tmp_path):
    """dread.lift_msp430 on random MSP430 code: every source and destination mode in word and byte
    form, the flags, dadd, push and pop, shared epilogues, calls (dread.liftfuzz)."""
    from dread import liftfuzz
    lines = []
    failures, refused, _ = liftfuzz.run("msp430", functions=16, length=60, inputs=16, seed=7,
                                        keep=str(tmp_path / "msp430"), out=lines.append, reduce=0)
    assert not failures and not refused, "\n".join(lines)


@pytest.mark.slow
@needs_ti_gcc
def test_a_planted_msp430_mistake_shows_and_is_reduced(tmp_path, monkeypatch):
    """jlo taken on carry set instead of clear: the fuzzer finds it and reduces it to a few units."""
    from dread import liftfuzz, lift_msp430
    monkeypatch.setitem(lift_msp430.JUMPS, "jlo", "c")
    lines = []
    failures, refused, _ = liftfuzz.run("msp430", functions=16, length=60, inputs=16, seed=7,
                                        keep=str(tmp_path / "msp430"), out=lines.append, reduce=1)
    assert failures and not refused, "\n".join(lines)
    reduced = [line for line in lines if line.startswith("reduced")]
    assert len(reduced) == 1 and "jlo" in reduced[0] and len(reduced[0].split(" to: ")[1].split(" | ")) <= 6, reduced


@pytest.mark.slow
@needs_ti_gcc
def test_lifted_c_that_calls_libgcc_fails_the_fuzzers_build(tmp_path, monkeypatch):
    """C that needs a libgcc routine the image does not hold (swpb as GCC's __builtin_bswap16, two
    shifts) fails the build; the fuzzer says so per function and reduces it to the swpb."""
    from dread import liftfuzz, lift_msp430
    monkeypatch.setattr(lift_msp430, "HELPERS", lift_msp430.HELPERS.replace(
        '__asm__ ("swpb %0" : "+r"(x));\n    return x;', "return __builtin_bswap16(x);"))
    lines = []
    failures, refused, _ = liftfuzz.run("msp430", functions=8, length=60, inputs=8, seed=1,
                                        keep=str(tmp_path / "msp430"), out=lines.append, reduce=1)
    assert failures and not refused, "\n".join(lines)
    assert any("undefined reference to `__mspabi_s" in block for block in failures.values())
    reduced = [line for line in lines if line.startswith("reduced")]
    assert len(reduced) == 1 and "swpb" in reduced[0].split(" to: ")[1].split(" | ")[-1], reduced


MSP_NAMES = ["fmt_dec", "read_line", "timer_irq", "memcmp", "cmd_volume", "__mspabi_mpyl",
             "udivmodsi4", "__mspabi_remul", "parse_kv"]        # (the last three: stack arguments)


def msp430_copy(tmp_path):
    """gizmo in tmp_path (its builds share sources), with the generated cases and the register flow
    gizmo-msp430 has cached."""
    from conftest import GIZMO
    from dread.target import Target
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir("msp430"), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, copy / "msp430" / "build" / cache)
    target = Target.load(str(copy / "msp430"))
    return target, target.image()


@pytest.mark.slow
@needs_ti_gcc
def test_gizmo_msp430_lift_check_accepts_what_agrees_and_drops_a_mistake(tmp_path, monkeypatch):
    from dread import build, lift, lift_msp430
    target, image = msp430_copy(tmp_path)
    lines = []
    accepted = lift.check(target, image, MSP_NAMES, out=lines.append, jobs=4)
    assert sorted(accepted) == sorted(MSP_NAMES), lines
    assert build.read_names(image.table("lifted")) == sorted(MSP_NAMES)
    monkeypatch.setitem(lift_msp430.JUMPS, "jne", "z")      # jne taken on Z set
    lines = []
    accepted = lift.check(target, image, MSP_NAMES, out=lines.append, jobs=4)
    dropped = [line.split()[2] for line in lines if line.startswith("dropped  FAIL")]
    assert dropped and set(accepted) | set(dropped) == set(MSP_NAMES), lines


@pytest.mark.slow
@needs_ti_gcc
def test_a_stack_argument_read_from_the_wrong_place_shows(tmp_path, monkeypatch):
    """udivmodsi4 takes on the stack whether its callers want the remainder. Read from the frame's
    top word instead (a planted mistake), __mspabi_remul, which passes 1 there from its frame,
    differs, and udivmodsi4 is not accepted: its own cases fail. (They pass the word in a cell at
    the stack top. The check that moves the stack left that cell behind until 2026-10-21, so every
    case changed with the stack moved and those that differed were refused, not failed; dropped as
    uncovered then, and accepted once the firm-up had found an input with the word 0, where the wrong
    place reads the same. The room for stack arguments moves with the stack since.)"""
    from dread import lift, lift_msp430
    target, image = msp430_copy(tmp_path)
    slot = lift_msp430.Msp430Lifter._slot
    monkeypatch.setattr(lift_msp430.Msp430Lifter, "_slot", lambda self, s: slot(self, s if s > -2 else 2))
    lines = []
    accepted = lift.check(target, image, ["udivmodsi4", "__mspabi_remul"], out=lines.append, jobs=4)
    assert "udivmodsi4" not in accepted, lines
    assert any(line.startswith("dropped  FAIL udivmodsi4") for line in lines), lines
    assert any(line.startswith("dropped  FAIL __mspabi_remul") for line in lines), lines


def test_an_msp430_interrupt_wakes_the_core_from_a_low_power_mode():
    """The system run's interrupt on the MSP430 (dread.arch.msp430.enter_interrupt), as TI's guide has
    it: main sets GIE and CPUOFF (LPM0) and the engine stops behind that instruction, asleep; the
    interrupt pushes pc and then sr, clears sr (awake, GIE off) and goes to vector 0; the handler
    clears CPUOFF in the stacked sr and returns with reti (in the engine), so main goes on awake."""
    import unicorn.unicorn_const as C
    arch = Msp430()
    e = arch.new_uc()
    e.mem_map(0x0000, 0x10000)
    e.mem_write(0x8000, bytes.fromhex("32d01800" "1c43" "ff3f"))           # bis #0x18, sr; mov #1, r12; jmp $
    e.mem_write(0x8100, bytes.fromhex("b1c010000000" "3d400700" "0013"))   # bic #0x10, 0(sp); mov #7, r13; reti
    e.mem_write(0xFFE0, struct.pack("<H", 0x8100))                         # vector 0
    e.mem_write(0xFFFE, struct.pack("<H", 0x8000))                         # reset
    pc = arch.reset(e, None, 0x8000, 0xFFE0)
    assert pc == 0x8000 and e.reg_read(2) == 0 and not arch.interrupts_enabled(e)
    e.reg_write(1, 0x0A00)
    e.emu_start(pc, 0x8006)
    resume = arch.resume_address(e)
    assert e.sleeping and resume == 0x8004 and arch.interrupts_enabled(e)
    pc = arch.enter_interrupt(e, 0, resume, 0xFFE0, None, None)
    assert pc == 0x8100 and e.reg_read(2) == 0 and e.reg_read(1) == 0x09FC
    assert struct.unpack("<HH", bytes(e.mem_read(0x09FC, 4))) == (0x18, 0x8004)    # sr below pc
    e.emu_start(pc, 0x8006)
    assert e.reg_read(0) == 0x8006 and not e.sleeping
    assert (e.reg_read(12), e.reg_read(13), e.reg_read(1), e.reg_read(2) & 0x18) == (1, 7, 0x0A00, 0x08)


@needs_ti_gcc
def test_gizmo_msp430_system_run(tmp_path):
    """gizmo-msp430 from reset with its models, original against the image with the rewritten C:
    every scenario agrees; in the keys scenario the timer's interrupt (vector 0, through the
    interrupt controller) moves the key into the ring buffer and the status line follows."""
    from dread import build, sysrun
    from dread.target import Target
    from test_runner import src_copy
    target = Target.load(gizmo_dir("msp430"))
    payload = build.build(target, target.image(), src=str(src_copy(tmp_path, "msp430")), out=str(tmp_path / "recon"),
                          quiet=True)
    for name in sorted(target.scenarios):
        assert sysrun.main(["--target", target.root, "--scenario", name, "--recon", payload]) == 0, name
    outcome = sysrun.run_image(target, target.image(), target.image().payload(), target.scenarios["keys"])
    assert outcome.reports["interrupts"].get(0) and "mode run volume 7" in outcome.reports["uart"]["sent"], outcome


def test_the_engine_stops_a_run_that_circles_in_known_code():
    """DREAD's own engine keeps the stall rule itself (it counts instructions): checksum over 1024
    bytes (its 32-bit steps loop in helpers on the 16-bit core: about 150 instructions a byte) stops
    with stall, returns without; the generator uses the rule on this engine."""
    from dread import autocase, emu
    from dread.target import Target
    target = Target.load(gizmo_dir("msp430"))
    gen = autocase.Generator(target, target.image())
    fn = gen.program.by_name["checksum"]
    scratch = target.memory.scratch_base

    def run(n, stall):
        m = gen.machine()
        m.plan = []
        m.write(scratch, bytes(range(256)) * 16)
        try:
            m.call(fn.entry, scratch, n & 0xFFFF, n >> 16, max_instructions=200_000, stall=stall)
            return "returned", m.stalled
        except emu.EmuError:
            return "stopped", m.stalled, m.timed_out
    assert run(1024, 0) == ("returned", False)
    assert run(16, 20_000) == ("returned", False)
    assert run(1024, 20_000) == ("stopped", True, True)
    assert gen.stall_part == autocase.STALL


CONTEXT_SWITCH_CODE = """
    .text
    .global yields
yields:                     ; keeps its argument across a context switch
    push r10
    mov r12, r10
    call #switch_to
    mov r10, r12
    add #1, r12
    pop r10
    ret
    .global switch_to
switch_to:                  ; saves r4 on this task's stack, takes the next task's stack pointer from cur
    push r4
    mov &0x0400, r12
    mov r1, 0(r12)
    call #pick
    mov &0x0400, r12
    mov @r12, r1
    pop r4
    ret
    .global pick
pick:                       ; the next task: its TCB pointer is in next
    mov &0x0402, &0x0400
    ret
"""


@needs_ti_gcc
def test_a_context_switch_takes_no_stack_arguments_and_is_stubbed_in_cases(tmp_path):
    """A function that loads its stack pointer from memory not on the stack (a context switch:
    FreeRTOS's vPortYield on the MSP430) is refused itself, takes the stack words it reads before
    the switch as its stack arguments (none here: its callers lift without [abi] stack_args), and
    the generated cases of a function that calls it stub it (run for real on FreeRTOS's MSP430
    build, it loaded the next task's stack pointer from a generated TCB and the inputs died; this
    small one survives on zeroed memory, so only the stub is checked here)."""
    from dread import autocase, lift
    target, image, program = msp430_asm_target(tmp_path, CONTEXT_SWITCH_CODE, "yields")
    lifter = lift.make_lifter(program)
    switch_to = program.by_name["switch_to"]
    assert lifter.switches_stacks(switch_to) and not lifter.switches_stacks(program.by_name["yields"])
    assert lifter.stack_args(switch_to) == 0
    with pytest.raises(lift.StackSwitch, match="loads the stack pointer from memory not on the stack"):
        lifter.lift(switch_to)
    assert "switch_to(" in lifter.lift(program.by_name["yields"]).text
    gen = autocase.Generator(target, image)
    stubs = gen.switch_stubs("yields")
    assert [s[0] for s in stubs] == [switch_to.entry]
    fn = program.by_name["yields"]
    cases = gen.generate("yields", fn.entry, fn.insns, calls=stubs)
    got = set()
    for c in cases:
        got |= gen.coverage(fn.entry, fn.insns, c, c.get("limit", 200_000))
    assert got == set(fn.insns)
    assert cases and all(switch_to.entry in [c[0] for c in case["calls"]] for case in cases)


FLAG_CODE = """
    .text
    .global is_zero
is_zero:                    ; 1 when the word at 28(r12) is zero (FreeRTOS's prvIsQueueEmpty)
    mov 28(r12), r13
    mov.b #1, r12
    tst r13
    jeq 1f
    clr.b r12
1:  ret
"""


@needs_ti_gcc
def test_a_flag_taken_as_a_value_calls_no_shift_in_libgcc(tmp_path):
    """GCC's tree phi optimisation turns the lifted `if (z) goto L; r12 = 0; L:` into
    (-z | z) >> 15, and on the MSP430 a shift by 15 is a call of libgcc's __mspabi_srli, which the
    original does not make (dread.lift.foreign_calls drops such a function: four of FreeRTOS's
    build). The MSP430 layer's lifted_cflags turn it off for GCC (-fno-ssa-phiopt)."""
    from dread import build, lift
    target, image, program = msp430_asm_target(tmp_path, FLAG_CODE, "is_zero")
    assert target.compiler.lifted_cflags == ["-Os", "-fno-ssa-phiopt"]
    lifter = lift.make_lifter(program)
    lifted = {"is_zero": lifter.lift(program.by_name["is_zero"])}
    lifter.lifted = lifted
    src = tmp_path / "lifted"
    src.mkdir()
    (src / "is_zero.c").write_text(lifted["is_zero"].text)
    found = {}
    for flags in (["-Os"], target.compiler.lifted_cflags):
        work = tmp_path / ("w%d" % len(found))
        build.compile_all(target, image, str(src), str(work / "obj-lifted"), flags=flags)
        found[" ".join(flags)] = lift.foreign_calls(program, str(work), ["is_zero"], lifter)
    assert found == {"-Os": {"is_zero": {"__mspabi_srli"}}, "-Os -fno-ssa-phiopt": {}}
