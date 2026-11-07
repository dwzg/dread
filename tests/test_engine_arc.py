"""The ARC engine (dread.engines.arc): its semantics on code assembled by arc-elf32-as (the bytes
are below, so these need no toolchain), and C compiled for the ARC600 run on the engine against the
same C run on the host (needs arc-elf32-gcc and the host's gcc). The engine contract itself is
tests/test_engines.py's, with the other engines."""
import ctypes
import random
import shutil
import subprocess

import pytest

from dread.arch import get
from dread.engines.arc import ArcEngine

ARC = get({"name": "arc", "isa": "arc600"})
CODE, DATA, STACK = 0x1000, 0x8000, 0x20000


def run(hexcode, regs=None, memory=None, count=1000):
    e = ArcEngine()
    e.mem_map(0, 0x40000)
    code = bytes.fromhex(hexcode)
    e.mem_write(CODE, code)
    for address, data in (memory or {}).items():
        e.mem_write(address, data)
    ARC.initial_status(e)
    e.reg_write(28, STACK)
    for r, v in (regs or {}).items():
        e.reg_write(r, v)
    e.emu_start(CODE, CODE + len(code), count=count)
    return e


def test_delay_slots_run_before_the_branch_goes():
    # bl.d 1f; mov r1,7 (its slot); mov r2,9; b 2f; 1: j_s.d [blink]; add_s r0,r1,1 (slot); 2: nop_s
    e = run("120820004a21c0014a22400209000000e07f0169e078")
    assert [e.reg_read(r) for r in (0, 1, 2)] == [8, 7, 9]
    assert e.reg_read(31) == CODE + 8           # bl.d returns behind its slot


def test_a_run_that_ends_in_front_of_a_delay_slot_goes_on_behind_it():
    # bl.d 1f; mov r1,7 (slot) ... as in the test above: one instruction per run
    e = ArcEngine()
    e.mem_map(0, 0x40000)
    code = bytes.fromhex("120820004a21c0014a22400209000000e07f0169e078")
    e.mem_write(CODE, code)
    e.reg_write(28, STACK)
    pc = CODE
    for _ in range(20):
        e.emu_start(pc, CODE + len(code), count=1)
        pc = e.reg_read(64)
        if pc == CODE + len(code):
            break
    assert [e.reg_read(r) for r in (0, 1, 2)] == [8, 7, 9]


def test_a_zero_overhead_loop_runs_lp_count_times():
    # mov lp_count,5; mov r0,0; lp 1f; add r0,r0,3; add r0,r0,1; 1: mov r1,r0
    e = run("4a2440714a200000a82080014020c000402040000a210000")
    assert (e.reg_read(0), e.reg_read(1), e.reg_read(60)) == (20, 20, 0)
    # cmp r0,0 makes Z: lpne fails and jumps to the loop's end, past the body
    e = run("4a2440714a2000004c200080e82022014020c0000a210000")
    assert (e.reg_read(0), e.reg_read(1), e.reg_read(60)) == (0, 0, 5)


FLAGS = "002042804126037000000000022044804326057000000a00ca266100ca276b00"


@pytest.mark.parametrize("a, b, want", [
    # add.f r2,r0,r1; adc r3,0,0; sub.f r4,r0,r1; sbc r5,10,0; mov.eq r6,1; mov.lt r7,1
    (0xFFFFFFFF, 1, (0, 1, 0xFFFFFFFE, 10, 0, 1)),        # a carry; no borrow
    (1, 2, (3, 0, 0xFFFFFFFF, 9, 0, 1)),                  # a borrow: sbc takes 1 more
    (0x80000000, 1, (0x80000001, 0, 0x7FFFFFFF, 10, 0, 1)),   # overflow: lt is N != V
    (5, 5, (10, 0, 0, 10, 1, 0)),
])
def test_carry_borrow_and_conditions(a, b, want):
    e = run(FLAGS, {0: a, 1: b})
    assert tuple(e.reg_read(r) for r in range(2, 8)) == want


def test_j_f_returns_from_an_interrupt_with_status32_l1():
    # sr r1,[status32_l1]; j.f [ilink1]
    e = ArcEngine()
    e.mem_map(0, 0x10000)
    e.mem_write(CODE, bytes.fromhex("6b21c00220204087"))
    e.reg_write(1, 0xA02)                       # Z, C and E1
    e.reg_write(29, 0x2000)
    e.emu_start(CODE, 0x2000, count=10)
    assert e.reg_read(64) == 0x2000 and e.aux_read(0xA) == 0xA02


def test_loads_and_stores_write_back_and_scale():
    # st.ab r1,[r0,4]; st.aw r2,[r0,4]; ld.as r3,[r0,-2]; ldb.x r4,[r0,-6]; ldh.x r5,[r0,-8]; ld r6,[r0,r7]
    e = run("0418500004188800fe100386fa10c480f81045813020c601", {0: DATA, 1: 0x80709234, 2: 0x55, 7: 0})
    assert e.reg_read(0) == DATA + 8
    assert bytes(e.mem_read(DATA, 12)) == bytes.fromhex("34927080" "00000000" "55000000")
    assert [e.reg_read(r) for r in (3, 4, 5, 6)] == [0x80709234, 0x70, 0xFFFF9234, 0x55]


@pytest.mark.parametrize("a, b, want", [(0xFFFFFFFF, 1, (0, 0, 1, 0)), (1, 2, (0, 1, 1, 1))])
def test_compare_and_branch(a, b, want):
    # brlt r0,r1,1f; mov r2,1; 1: bbit1 r0,31,2f; mov r3,1; 2: brhs.d r0,r1,3f; mov r4,1 (slot); mov r5,1
    e = run("090842004a2240000908df074a2340000d0865004a2440004a254000e078", {0: a, 1: b})
    assert tuple(e.reg_read(r) for r in (2, 3, 4, 5)) == want


def test_flag_sets_the_flags_and_halts():
    # flag 0x900; mov r0,1; flag 1 (H: halt); mov r1,1
    e = run("2920800f000000094a204000692040004a214000", count=2)
    assert ARC.flags_value(e) == 0x900 and not e.halted
    e = run("2920800f000000094a204000692040004a214000")
    assert (e.reg_read(0), e.reg_read(1), e.halted) == (1, 0, True)
    assert e.reg_read(64) == CODE + 16 and ARC.flags_value(e) == 0      # (flag 1 wrote them all)


def test_mul64_writes_mlo_and_mhi():
    # mul64 r0,r1; mov r2,mlo; mov r3,mhi; mulu64 r0,r1; mov r4,mhi
    e = run("04287e000a22400e0a23c00e05287e000a24c00e", {0: 0xFFFFFFFE, 1: 3})
    assert [e.reg_read(r) for r in (2, 3, 4)] == [0xFFFFFFFA, 0xFFFFFFFF, 2]


def test_code_written_while_running_is_decoded_again():
    e = ArcEngine()
    e.mem_map(0, 0x10000)
    e.mem_write(CODE, bytes.fromhex("4a204000"))            # mov r0,1
    e.emu_start(CODE, CODE + 4)
    e.mem_write(CODE, bytes.fromhex("4a208000"))            # mov r0,2
    e.emu_start(CODE, CODE + 4)
    assert e.reg_read(0) == 2


# ---- C for the ARC600 against the same C on the host ----

C_SOURCE = r"""
typedef unsigned int u32; typedef int s32; typedef unsigned long long u64; typedef long long s64;
typedef unsigned short u16; typedef short s16; typedef unsigned char u8; typedef signed char s8;
u8 buf[256];
static const u32 tab[8] = {3, 1, 4, 1, 5, 9, 2, 6};
u32 f_add(u32 a, u32 b, u32 c) { return a + b * 3 + c; }
u32 f_sub(u32 a, u32 b, u32 c) { return a - b - (c << 2); }
u32 f_lt(u32 a, u32 b, u32 c) { return ((s32)a < (s32)b) + 2 * (a < b) + 4 * ((s32)a >= (s32)c) + 8 * (a == c); }
u32 f_shift(u32 a, u32 b, u32 c) { return (a << (b & 31)) ^ (a >> (c & 31)) ^ (u32)((s32)a >> ((b + c) & 31)); }
u32 f_cshift(u32 a, u32 b, u32 c) { return (a << 3) + (b >> 7) + (u32)((s32)c >> 13) + (a >> 31); }
u32 f_mul(u32 a, u32 b, u32 c) { return a * b + c * 7; }
u32 f_div(u32 a, u32 b, u32 c) { if (!b) b = 1; if (!c) c = 3;
    return a / b + a % c + (u32)((s32)a / (s32)((c & 0x7fff) | 1)) + (u32)((s32)b % (s32)((a & 0xfff) + 1)); }
u32 f_64(u32 a, u32 b, u32 c) { u64 x = ((u64)a << 32) | b; u64 y = (u64)c * 1000003u; x += y; x -= (u64)b << 7;
    return (u32)(x >> 17) ^ (u32)x; }
u32 f_s64(u32 a, u32 b, u32 c) { s64 x = (s64)(s32)a * (s32)b; return (u32)(x >> 32) + (u32)x + (x < (s64)(s32)c); }
u32 f_udiv64(u32 a, u32 b, u32 c) { u64 x = ((u64)a << 32) | b; u64 d = c | 1; return (u32)(x / d) ^ (u32)(x % d); }
u32 f_loop(u32 a, u32 b, u32 c) { u32 s = 0; for (u32 i = 0; i < (b & 63); i++) s += a ^ (i * c); return s; }
u32 f_loop2(u32 a, u32 b, u32 c) { u32 s = 1; u32 n = (a & 15) + 1; while (n--) { s = s * 31 + b; b ^= c; } return s; }
u32 f_bytes(u32 a, u32 b, u32 c) { for (int i = 0; i < 32; i++) buf[i] = (u8)(a + i * b); s32 s = 0;
    for (int i = 0; i < 32; i++) s += (s8)buf[i]; return (u32)s ^ buf[c & 31]; }
u32 f_half(u32 a, u32 b, u32 c) { u16 *h = (u16 *)buf; for (int i = 0; i < 16; i++) h[i] = (u16)(a * i + b); s32 s = 0;
    for (int i = 0; i < 16; i++) s += (s16)h[i]; return (u32)s + h[c & 15]; }
u32 f_switch(u32 a, u32 b, u32 c) { switch (a & 15) { case 0: return b; case 1: return c; case 2: return b + c;
    case 3: return b - c; case 4: return b * c; case 5: return b ^ c; case 6: return b | 1; case 7: return ~c;
    case 9: return 99; case 12: return b >> 3; default: return tab[a & 7]; } }
u32 f_sext(u32 a, u32 b, u32 c) { return (u32)(s32)(s8)a + (u32)(s32)(s16)b + (u8)c + (u16)(a >> 8); }
u32 f_bits(u32 a, u32 b, u32 c) { return (a | (1u << (b & 31))) ^ (c & ~(1u << (a & 31))) ^ ((a >> (c & 31)) & 1) ^ (b & 0x1ff); }
static u32 rec(u32 n) { return n < 2 ? n : rec(n - 1) + rec(n - 2); }
u32 f_rec(u32 a, u32 b, u32 c) { return rec(a & 15) + b; }
u32 f_minmax(u32 a, u32 b, u32 c) { s32 x = (s32)a, y = (s32)b; s32 m = x > y ? x : y; s32 n = x < (s32)c ? x : (s32)c;
    return (u32)m * 3 + (u32)n; }
u32 f_cond(u32 a, u32 b, u32 c) { u32 r = a; if (a > b) r = c; if ((s32)c < 0) r += 5; if (b == 7) r ^= a; return r; }
u32 f_abs(u32 a, u32 b, u32 c) { s32 x = (s32)a; s32 y = x < 0 ? -x : x; return (u32)y + (b != 0) + (c == 0); }
u32 f_neg(u32 a, u32 b, u32 c) { return -a + (~b) + (u32)(-(s32)c > 0); }
typedef u32 (*fp)(u32, u32, u32);
u32 f_call(u32 a, u32 b, u32 c) { static const fp fs[4] = {f_add, f_sub, f_mul, f_bits};
    return fs[a & 3](a, b, c) + fs[b & 3](c, a, b); }
u32 f_strlen(u32 a, u32 b, u32 c) { for (int i = 0; i < 40; i++) buf[i] = (u8)("hello, world. abcdefghijklmnopqrstuvwxyz"[i]);
    buf[(a % 39)] = 0; u32 n = 0; while (buf[n]) n++; return n * 100 + buf[b % 40]; }
u32 f_memcpy(u32 a, u32 b, u32 c) { u32 w[8]; for (int i = 0; i < 8; i++) w[i] = a * (i + 1) ^ b; u8 *d = buf + (c & 7);
    u8 *s = (u8 *)w; for (int i = 0; i < 32; i++) d[i] = s[i]; u32 r = 0; for (int i = 0; i < 32; i++) r = r * 3 + d[i];
    return r; }
u32 f_carry(u32 a, u32 b, u32 c) { u32 s = a + b; u32 carry = s < a; u32 t = c - a; u32 borrow = c < a;
    return s ^ (carry << 4) ^ t ^ (borrow << 9); }
"""
LINKER = """SECTIONS { . = 0x1000; .text : { *(.text .text.*) } .rodata : { *(.rodata .rodata.*) } . = ALIGN(4);
.data : { *(.data .data.* .sdata .sdata.*) } .bss : { *(.bss .bss.* .sbss .sbss.* COMMON) }
/DISCARD/ : { *(.comment) *(.note*) } }"""
VALUES = [0, 1, 2, 3, 7, 31, 32, 0x7F, 0x80, 0xFF, 0x100, 0x7FFF, 0x8000, 0xFFFF, 0x10000, 0x7FFFFFFF,
          0x80000000, 0xFFFFFFFE, 0xFFFFFFFF]
SENTINEL = 0xFFF0


@pytest.mark.skipif(not (shutil.which("arc-elf32-gcc") and shutil.which("gcc")), reason="needs arc-elf32-gcc and gcc")
@pytest.mark.parametrize("opt", ["-Os", "-O2", "-O0"])
def test_c_for_the_arc600_computes_what_the_host_computes(tmp_path, opt):
    """libgcc's multiply and divide (a zero-overhead loop, rlc, adc, sbc), 64-bit arithmetic,
    switch tables, bytes and halfwords with sign extension, calls through pointers, recursion."""
    from elftools.elf.elffile import ELFFile
    src, ld, elf, so = tmp_path / "t.c", tmp_path / "t.ld", tmp_path / "t.elf", tmp_path / "host.so"
    src.write_text(C_SOURCE)
    ld.write_text(LINKER)
    subprocess.run(["arc-elf32-gcc", "-mcpu=arc600", opt, "-ffreestanding", "-fno-builtin", "-nostdlib",
                    "-T", str(ld), "-Wl,--no-warn-rwx-segments", "-Wl,-e,0x1000", "-o", str(elf), str(src),
                    "-lgcc"], check=True)
    subprocess.run(["gcc", "-O1", "-shared", "-fPIC", "-o", str(so), str(src)], check=True)
    host = ctypes.CDLL(str(so))
    e = ArcEngine()
    e.mem_map(0, 0x100000)
    with open(elf, "rb") as fh:
        f = ELFFile(fh)
        for seg in f.iter_segments():
            if seg["p_type"] == "PT_LOAD":
                e.mem_write(seg["p_paddr"], seg.data())
        functions = {s.name: s["st_value"] for s in f.get_section_by_name(".symtab").iter_symbols()
                     if s.name.startswith("f_")}
    assert len(functions) > 25
    rng = random.Random(opt)
    wrong = []
    for name, address in sorted(functions.items()):
        want_of = getattr(host, name)
        want_of.restype, want_of.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 3
        for _ in range(80):
            args = [rng.choice(VALUES) if rng.random() < 0.4 else rng.getrandbits(32) for _ in range(3)]
            ARC.initial_status(e)
            for i, a in enumerate(args):
                e.reg_write(i, a)
            e.reg_write(28, 0x80000)
            e.reg_write(31, SENTINEL)
            e.emu_start(address, SENTINEL, count=2_000_000)
            got, want = e.reg_read(0), want_of(*args)
            if got != want or e.reg_read(28) != 0x80000:
                wrong.append((name, [hex(a) for a in args], hex(want), hex(got)))
    assert not wrong, wrong[:10]
