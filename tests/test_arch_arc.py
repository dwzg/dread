"""The ARC layer's decoding and flow, against ground truth from binutils' ARC assembler.

The vectors below were produced by assembling the instructions for an ARC600 (arc-elf32-as) and
reading the bytes and resolved branch targets back with arc-elf32-objdump, so the test needs no
toolchain of its own. Each row is (address, little-endian bytes, size, flow). The instructions were
laid out from address 0, the branches targeting the start (label `t` at 0), which fixes the
expected targets.
"""

from dread.arch import get

ARC = get({"name": "arc", "isa": "arc600"})

# address, bytes, size, (kind, target, falls)
VECTORS = [
    (0x00, "4a260070", 4, ("next", None, True)),          # nop
    (0x04, "2870", 2, ("next", None, True)),              # mov_s r0,r1
    (0x06, "0a20800f34127856", 8, ("next", None, True)),  # mov r0,0x12345678  (long immediate)
    (0x0E, "00218000", 4, ("next", None, True)),          # add r0,r1,r2
    (0x12, "0021800f00000010", 8, ("next", None, True)),  # add r0,r1,0x1000   (long immediate)
    (0x1A, "04110000", 4, ("next", None, True)),          # ld r0,[r1,4]
    (0x1E, "04190000", 4, ("next", None, True)),          # st r0,[r1,4]
    (0x22, "e107cfff", 4, ("jump", 0x00, False)),         # b t        (unconditional)
    (0x26, "de0fcfff", 4, ("call", 0x00, True)),          # bl t
    (0x2A, "d807c1ff", 4, ("jump", 0x00, True)),          # beq t      (conditional, falls through)
    (0x2E, "2020c007", 4, ("return", None, False)),       # j [blink]
    (0x32, "22208000", 4, ("icall", None, True)),         # jl [r2]
    (0x36, "cd084080", 4, ("jump", None, True)),          # breq r0,r1,t  (target not decoded yet)
    (0x3A, "0181", 2, ("next", None, True)),              # ld_s r0,[r1,4]
    (0x3C, "5861", 2, ("next", None, True)),              # add_s r0,r1,r2
    (0x3E, "02218000", 4, ("next", None, True)),          # sub r0,r1,r2
    (0x42, "0c204080", 4, ("next", None, True)),          # cmp r0,r1
    (0x46, "e07e", 2, ("next", None, True)),              # j_s [blink]  (16-bit flow: follow-up)
    # 16-bit instructions that carry a long immediate (h-register = 0x3e), so 6 bytes
    (0x50, "cf7034127856", 6, ("next", None, True)),      # mov_s r0,0x12345678
    (0x58, "d77100000010", 6, ("next", None, True)),      # cmp_s r1,0x1000
    # BRcc: a register second operand and a limm are 4/8 bytes; a u6 (even u6 = 62) is 4, not a limm
    (0x60, "f5084180", 4, ("jump", None, True)),          # brne r0,r1,t   (register)
    (0x68, "d908918f", 4, ("jump", None, True)),          # brne r0,62,t   (u6, not a long immediate)
    (0x70, "ed08828f34127856", 8, ("jump", None, True)),  # brlt r0,0x12345678,t  (long immediate)
]


def test_instruction_lengths_match_the_assembler():
    for address, hexbytes, size, _ in VECTORS:
        code = bytes.fromhex(hexbytes)
        assert ARC.insn_length(code, "arc600") == size, hexbytes
        assert ARC.decode(code, address).size == size, hexbytes


def test_long_immediate_adds_a_word():
    # The two instructions with a limm operand (0x3e) are 8 bytes; their non-limm twins are 4.
    assert ARC.insn_length(bytes.fromhex("0a20800f34127856"), "arc600") == 8   # mov r0,limm
    assert ARC.insn_length(bytes.fromhex("00218000"), "arc600") == 4           # add r0,r1,r2
    assert ARC.insn_length(bytes.fromhex("0021800f00000010"), "arc600") == 8   # add r0,r1,limm


def test_flow_matches_the_assembler():
    for address, hexbytes, size, expected in VECTORS:
        insn = ARC.decode(bytes.fromhex(hexbytes), address)
        assert ARC.flow(insn) == expected, hexbytes


def test_a_short_buffer_decodes_nothing_past_its_end():
    # The 8-byte limm instruction in only 4 bytes cannot be decoded.
    assert ARC.decode(bytes.fromhex("0a20800f"), 0) is None


def test_registers():
    assert ARC.reg_name("blink") == "r31"
    assert ARC.reg_id("sp") == 28
    assert ARC.reg_id("r0") == 0
    assert ARC.arg_regs(10) == ["r%d" % i for i in range(8)]
    assert ARC.isa_bit == 0
