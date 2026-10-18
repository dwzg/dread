"""Write a target's src/addresses.h: where the rewritten C (../rewritten) finds the original's
data. Taken from the ELF here because gizmo is our own build; a real reconstruction writes these
from its reading of the listing."""
import struct
import sys

sys.path.insert(0, "../../..")
from dread.elf import Elf  # noqa: E402

elf_path, out_path, variant = sys.argv[1:4]
elf = Elf(elf_path)
syms = {s.name: s.value for s in elf.symbols if s.name and not s.name.startswith("$")}
data, base = elf.payload()


def string_at(address):
    o = address - base
    if not 0 <= o < len(data):
        return None
    end = data.find(b"\0", o)
    return data[o:end] if end >= 0 else None


def mode_strings():
    """mode_name's strings for modes 0-7: from its switch table (gcc names it CSWTCH, clang's has
    no symbol: found by what it points to), else from the strings themselves."""
    want = [b"off", b"idle", b"run", b"test", b"?", b"?", b"?", b"service"]
    if "CSWTCH.24" in syms:
        return struct.unpack_from("<8I", data, syms["CSWTCH.24"] - base)
    for o in range(0, len(data) - 32, 4):
        words = struct.unpack_from("<8I", data, o)
        if [string_at(w) for w in words] == want:
            return words
    found = {}
    for text in set(want):
        o = data.find(b"\0" + text + b"\0")
        found[text] = base + o + 1
    return [found[t] for t in want]


names = mode_strings()
bases = {"a32": (0x80000000, 0x80003000), "m3": (0x40011000, 0x40010800), "rv32": (0x10000000, 0x10003000),
         "msp430": (0x0100, 0x0130)}
lines = ["/* written by examples/gizmo/fw/addresses.py from %s; do not edit */" % elf_path.split("/")[-1],
         "#define UART_BASE 0x%08xu" % bases[variant][0],
         "#define GPIO_BASE 0x%08xu" % bases[variant][1],
         ("#define COMMANDS 0x%08xu" % syms["commands"]) if "commands" in syms else
         # clang inlined dispatch's handlers: no table, the rewritten dispatch stores into g_config itself
         "#define INLINED_COMMANDS 1\n#define G_CONFIG 0x%08xu" % syms["g_config"],
         "#define STR_OFF ((const char *)0x%08x)" % names[0],
         "#define STR_IDLE ((const char *)0x%08x)" % names[1],
         "#define STR_RUN ((const char *)0x%08x)" % names[2],
         "#define STR_TEST ((const char *)0x%08x)" % names[3],
         "#define STR_UNKNOWN ((const char *)0x%08x)" % names[4],
         "#define STR_SERVICE ((const char *)0x%08x)" % names[7]]
if variant in ("m3", "rv32", "msp430"):     # (msp430: C division calls the image's own __mspabi_divul)
    lines.append("#define HAVE_UDIV 1")
if variant == "msp430":
    lines.append("#define HWREG(a) (*(volatile u16 *)(a))")
with open(out_path, "w") as f:
    f.write("\n".join(lines) + "\n")
