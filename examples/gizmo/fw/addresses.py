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
table = syms["CSWTCH.24"]
names = struct.unpack_from("<8I", data, table - base)     # mode_name's strings, modes 0-7
lines = ["/* written by examples/gizmo/fw/addresses.py from %s; do not edit */" % elf_path.split("/")[-1],
         "#define UART_BASE 0x%08xu" % (0x80000000 if variant == "a32" else 0x40011000),
         "#define GPIO_BASE 0x%08xu" % (0x80003000 if variant == "a32" else 0x40010800),
         "#define COMMANDS 0x%08xu" % syms["commands"],
         "#define STR_OFF ((const char *)0x%08x)" % names[0],
         "#define STR_IDLE ((const char *)0x%08x)" % names[1],
         "#define STR_RUN ((const char *)0x%08x)" % names[2],
         "#define STR_TEST ((const char *)0x%08x)" % names[3],
         "#define STR_UNKNOWN ((const char *)0x%08x)" % names[4],
         "#define STR_SERVICE ((const char *)0x%08x)" % names[7]]
if variant == "m3":
    lines.append("#define HAVE_UDIV 1")
with open(out_path, "w") as f:
    f.write("\n".join(lines) + "\n")
