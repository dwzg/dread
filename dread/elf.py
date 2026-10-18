"""A small reader of 32-bit little-endian ELF files: sections, program headers, symbols.

Enough for what DREAD takes from an ELF: the image bytes as they lie in flash (what
`objcopy -O binary` writes), the sections the startup copies elsewhere, the function and data
symbols, and ARM's mapping symbols ($a, $t, $d), which mark code of either instruction set and data
inside a section. No binutils needed.
"""
import struct

SHT_NOBITS = 8
SHT_SYMTAB = 2
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4
PT_LOAD = 1
STT_OBJECT, STT_FUNC = 1, 2


class ElfError(Exception):
    pass


class Section:
    def __init__(self, name, kind, flags, addr, offset, size, link):
        self.name, self.kind, self.flags, self.addr = name, kind, flags, addr
        self.offset, self.size, self.link = offset, size, link
        self.lma = addr                         # set from the program headers

    @property
    def alloc(self):
        return bool(self.flags & SHF_ALLOC)

    @property
    def code(self):
        return bool(self.flags & SHF_EXECINSTR)

    @property
    def nobits(self):
        return self.kind == SHT_NOBITS

    def __repr__(self):
        return "Section(%s, vma 0x%x, lma 0x%x, 0x%x bytes)" % (self.name, self.addr, self.lma, self.size)


class Symbol:
    def __init__(self, name, value, size, kind, bind, shndx):
        self.name, self.value, self.size, self.kind, self.bind, self.shndx = name, value, size, kind, bind, shndx

    def __repr__(self):
        return "Symbol(%s, 0x%x, %d)" % (self.name, self.value, self.size)


class Elf:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.raw = f.read()
        raw = self.raw
        if raw[:4] != b"\x7fELF":
            raise ElfError("%s is not an ELF file" % path)
        if raw[4] != 1 or raw[5] != 1:
            raise ElfError("%s: only 32-bit little-endian ELF is supported" % path)
        (self.type, self.machine, _, self.entry, phoff, shoff, self.flags, _, phentsize, phnum,
         shentsize, shnum, shstrndx) = struct.unpack_from("<HHIIIIIHHHHHH", raw, 16)
        self.sections = []
        heads = [struct.unpack_from("<IIIIIIIIII", raw, shoff + i * shentsize) for i in range(shnum)]
        names = heads[shstrndx][4] if shnum else 0
        for (name, kind, flags, addr, offset, size, link, _, _, _) in heads:
            self.sections.append(Section(self._str(names, name), kind, flags, addr, offset, size, link))
        self.segments = []
        for i in range(phnum):
            (kind, offset, vaddr, paddr, filesz, memsz, flags, _) = struct.unpack_from(
                "<IIIIIIII", raw, phoff + i * phentsize)
            self.segments.append((kind, offset, vaddr, paddr, filesz, memsz, flags))
        for s in self.sections:
            if not s.alloc:
                continue
            for (kind, offset, vaddr, paddr, filesz, memsz, flags) in self.segments:
                if kind == PT_LOAD and vaddr <= s.addr < vaddr + max(memsz, 1):
                    s.lma = paddr + (s.addr - vaddr)
                    break
        self.symbols = []
        for s in self.sections:
            if s.kind != SHT_SYMTAB:
                continue
            strtab = self.sections[s.link].offset
            for i in range(s.size // 16):
                (name, value, size, info, _, shndx) = struct.unpack_from("<IIIBBH", raw, s.offset + i * 16)
                self.symbols.append(Symbol(self._str(strtab, name), value, size, info & 0xF, info >> 4, shndx))

    def _str(self, table, offset):
        end = self.raw.index(b"\0", table + offset)
        return self.raw[table + offset:end].decode("latin-1")

    def section(self, name):
        for s in self.sections:
            if s.name == name:
                return s
        return None

    def section_of(self, symbol):
        if 0 < symbol.shndx < len(self.sections):
            return self.sections[symbol.shndx]
        return None

    def loaded(self):
        """The allocated sections with contents, as [(lma, bytes, section)], by address."""
        out = []
        for s in self.sections:
            if s.alloc and not s.nobits and s.size:
                out.append((s.lma, self.raw[s.offset:s.offset + s.size], s))
        return sorted(out, key=lambda x: x[0])

    def payload(self, base=None, fill=0xFF):
        """The image as `objcopy -O binary` writes it: every allocated section with contents at its
        load address, gaps filled with `fill`, from `base` (default: the lowest load address)."""
        parts = self.loaded()
        if not parts:
            return b"", base or 0
        start = parts[0][0] if base is None else base
        end = max(lma + len(data) for lma, data, _ in parts)
        out = bytearray([fill]) * (end - start)
        for lma, data, _ in parts:
            if lma < start:
                raise ElfError("section at 0x%x lies before the base 0x%x" % (lma, start))
            out[lma - start:lma - start + len(data)] = data
        return bytes(out), start

    def mapping(self):
        """ARM mapping symbols as {section index: [(address, kind)]}, kind "a32", "t32" or "data",
        sorted by address. GNU as writes $a, $t and $d, sometimes with a suffix ($d.1)."""
        out = {}
        for sym in self.symbols:
            head = sym.name.split(".", 1)[0]
            kind = {"$a": "a32", "$t": "t32", "$d": "data"}.get(head)
            if kind and sym.shndx and sym.shndx < len(self.sections):
                out.setdefault(sym.shndx, []).append((sym.value, kind))
        for v in out.values():
            v.sort()
        return out
