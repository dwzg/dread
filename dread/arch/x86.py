"""The x86 layer: 32-bit x86 (i386) in flat protected mode, on capstone and Unicorn.

The registers are eax, ecx, edx, ebx, esp, ebp, esi and edi (a write to al, ah or ax is a write to
eax that keeps the rest of it, so it reads eax too), eip, and the six arithmetic flags of eflags as
"flags". A call pushes its return address (link_reg None: the emulator's push_return and pop_return,
as for MSP430). Instructions take 1 to 15 bytes (max_insn: the program database reads that many to
learn a length). The analysis decodes in Intel syntax (the destination first in capstone's operand
list); the listing writes AT&T syntax, which GNU as reads back (X86Asm), branches and calls to labels.

The calling convention is the System V i386 one (cdecl) by default: every argument on the stack
(default_args 0), the result in eax (edx:eax for 64 bits), ebx, esi, edi and ebp kept; a target's
[abi] may state another (args = 3 with eax, edx, ecx for GCC's -mregparm=3).

Not yet: interrupts (the IDT), the x87, MMX and SSE registers, string instructions beyond what the
analysis needs, 16-bit and 64-bit modes.
"""
import struct

import capstone as cs
from capstone import x86 as X
from unicorn import UC_ARCH_X86, UC_MODE_32, Uc
from unicorn import x86_const as UX

NAMES = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi"]
# the full register of each name capstone may report (the 8- and 16-bit parts of the eight)
FULL = {"al": "eax", "ah": "eax", "ax": "eax", "eax": "eax",
        "cl": "ecx", "ch": "ecx", "cx": "ecx", "ecx": "ecx",
        "dl": "edx", "dh": "edx", "dx": "edx", "edx": "edx",
        "bl": "ebx", "bh": "ebx", "bx": "ebx", "ebx": "ebx",
        "spl": "esp", "sp": "esp", "esp": "esp",
        "bpl": "ebp", "bp": "ebp", "ebp": "ebp",
        "sil": "esi", "si": "esi", "esi": "esi",
        "dil": "edi", "di": "edi", "edi": "edi",
        "eflags": "flags", "flags": "flags", "eip": "pc", "ip": "pc"}
PARTS = {name for name, full in FULL.items() if full in NAMES and name != full}
UC_REGS = {"eax": UX.UC_X86_REG_EAX, "ecx": UX.UC_X86_REG_ECX, "edx": UX.UC_X86_REG_EDX,
           "ebx": UX.UC_X86_REG_EBX, "esp": UX.UC_X86_REG_ESP, "ebp": UX.UC_X86_REG_EBP,
           "esi": UX.UC_X86_REG_ESI, "edi": UX.UC_X86_REG_EDI, "pc": UX.UC_X86_REG_EIP,
           "eip": UX.UC_X86_REG_EIP, "sp": UX.UC_X86_REG_ESP, "flags": UX.UC_X86_REG_EFLAGS,
           "eflags": UX.UC_X86_REG_EFLAGS}
# conditional jumps (and the counted ones), each with the condition it tests
CONDITIONS = ["o", "no", "b", "ae", "e", "ne", "be", "a", "s", "ns", "p", "np", "l", "ge", "le", "g"]
JUMPS = {"j" + c: c for c in CONDITIONS}
JUMPS.update({"jecxz": "ecxz", "jcxz": "cxz", "loop": "loop", "loope": "loope", "loopne": "loopne"})
EFLAGS_INIT = 0x2                           # bit 1 is always set


class X86:
    """The 32-bit x86 layer; the methods are those dread.arch.arm.Arm documents."""

    name = "x86"
    word = 4
    reg_bits = 32
    link_reg = None                         # a call pushes the return address
    isa_bit = 0                             # code lies at any byte, odd ones too (dread.arch.code_address)
    max_insn = 15                           # bytes an instruction may take
    default_args = 0                        # cdecl: every argument on the stack
    default_callee_saved = ("ebx", "esi", "edi", "ebp")
    default_result = ("eax", "edx")
    profile = None

    def __init__(self, isa="i386", endian="little", ghidra=None, cpu=None, extensions=None):
        if isa != "i386":
            raise ValueError("x86: only isa = \"i386\" (32-bit) is supported yet, not %r" % (isa,))
        if endian != "little":
            raise ValueError("x86 is little endian")
        self.isa = isa
        self.endian = endian
        self.cpu = cpu
        self.ghidra = ghidra or "x86:LE:32:default"
        self._md = cs.Cs(cs.CS_ARCH_X86, cs.CS_MODE_32)
        self._md.detail = True

    def configure(self, cflags):
        """Nothing to settle from the compiler's flags yet (-mregparm is the target's [abi] args)."""

    def describe(self):
        return "x86, 32-bit (i386), flat protected mode"

    # ---- registers ----

    def reg_name(self, name):
        return FULL.get(name, name)

    def reg_id(self, name):
        try:
            return UC_REGS[FULL.get(name, name)]
        except KeyError:
            raise ValueError("x86: no register %r" % (name,))

    def arg_regs(self, n):
        """The registers the first n arguments travel in: none for cdecl (n 0); GCC's -mregparm=n puts
        them in eax, edx and ecx."""
        if n > 3:
            raise ValueError("x86: at most three register arguments (eax, edx, ecx), not %d" % n)
        return ["eax", "edx", "ecx"][:n]

    def general_regs(self):
        return list(NAMES)

    @staticmethod
    def _name(name):
        return FULL.get(name, name)

    # ---- decoding ----

    def isas(self):
        return ("i386",)

    def min_insn(self, isa):
        return 1

    def insn_length(self, code, isa):
        insn = next(self._md.disasm(code, 0, 1), None)
        if insn is None:
            from dread.target import TargetError
            raise TargetError("x86: no instruction decodes from %s" % bytes(code[:self.max_insn]).hex())
        return insn.size

    def disassembler(self, isa=None):
        return self._md

    def decode(self, code, address, isa=None):
        return next(self._md.disasm(code, address, 1), None)

    def pc_value(self, address, isa):
        return address

    # ---- control flow ----

    def flow(self, insn):
        """(kind, target, falls), as Arm.flow documents: jmp to a label is a jump, through a register
        or memory an indirect jump (its register's name where it is one); the conditional jumps (and
        jecxz, loop) jump with the next instruction behind them; call to a label is a call, through a
        register or memory an indirect call; ret, retf and iret return; int and syscall-like
        instructions are indirect calls (their handler may change any register); hlt, ud2 and int3
        stop."""
        m = insn.mnemonic
        if m == "jmp":
            op = insn.operands[0] if insn.operands else None
            if op is not None and op.type == X.X86_OP_IMM:
                return "jump", op.imm & 0xFFFFFFFF, False
            if op is not None and op.type == X.X86_OP_REG:
                return "ijump", self._name(insn.reg_name(op.reg)), False
            return "ijump", None, False
        if m in JUMPS:
            op = insn.operands[0]
            return "jump", op.imm & 0xFFFFFFFF, True
        if m == "call":
            op = insn.operands[0] if insn.operands else None
            if op is not None and op.type == X.X86_OP_IMM:
                return "call", op.imm & 0xFFFFFFFF, True
            return "icall", None, True
        if m in ("ret", "retf", "iret", "iretd"):
            return "return", None, False
        if m in ("int", "into", "syscall", "sysenter"):
            return "icall", None, True
        if m in ("hlt", "ud2", "int3", "ud1", "ud0"):
            return "stop", None, False
        return "next", None, False

    def target(self, insn):
        kind, target, _ = self.flow(insn)
        return target if kind in ("jump", "call") else None

    def is_call(self, insn):
        return self.flow(insn)[0] in ("call", "icall")

    def is_syscall(self, insn):
        return insn.mnemonic in ("int", "into", "syscall", "sysenter")

    def register_call_targets(self, program, fn):
        return {}

    def is_conditional(self, insn):
        return insn.mnemonic in JUMPS

    def predicate(self, insn):
        """The condition a conditional jump tests (its mnemonic without the j), else None; cmovcc and
        setcc are no predicated instructions (they always write their destination, or read it)."""
        return JUMPS.get(insn.mnemonic)

    @staticmethod
    def complement(predicate):
        """The opposite condition: o/no, b/ae, e/ne, be/a, s/ns, p/np, l/ge, le/g."""
        if predicate in CONDITIONS:
            i = CONDITIONS.index(predicate)
            return CONDITIONS[i ^ 1]
        return None

    def condition(self, insn):
        return self.predicate(insn)

    def switches_isa(self, insn):
        return False

    def link_pair(self, prev, insn, isa):
        return False

    # ---- what instructions read and write ----

    def reg_effects(self, insn):
        """(reads, writes): the registers an instruction reads and writes by name (the eight, "flags",
        "pc"), from capstone's lists with the implicit ones (push and pop: esp; mul and div: eax, edx;
        call and ret: esp). A write to a register's part (al, ax) keeps the rest: it reads the whole
        register too."""
        try:
            r, w = insn.regs_access()
        except cs.CsError:
            r, w = (), ()
        reads, writes = set(), set()
        for reg in r:
            name = insn.reg_name(reg)
            reads.add(self._name(name))
        for reg in w:
            name = insn.reg_name(reg)
            full = self._name(name)
            writes.add(full)
            if name in PARTS:
                reads.add(full)
        if insn.mnemonic in ("xor", "sub", "sbb") and len(insn.operands) == 2 and \
                all(o.type == X.X86_OP_REG for o in insn.operands) and insn.operands[0].reg == insn.operands[1].reg:
            reads.discard(self._name(insn.reg_name(insn.operands[0].reg)))     # (xor eax, eax: 0; sbb: the carry)
        reads.discard("pc")
        writes.discard("pc")
        return reads, writes

    def sets_flags(self, insn):
        try:
            _, w = insn.regs_access()
        except cs.CsError:
            return False
        return any(insn.reg_name(reg) == "eflags" for reg in w)

    def stack_kind(self, insn):
        """"push" for push, "pop" for pop (of a register, memory or an immediate), else None."""
        if insn.mnemonic in ("push", "pushal", "pushfd"):
            return "push"
        if insn.mnemonic in ("pop", "popal", "popfd"):
            return "pop"
        return None

    def stack_bytes(self, insn):
        """How far a push or pop moves esp."""
        if insn.mnemonic in ("pushal", "popal"):
            return 32
        return 4 if self.stack_kind(insn) else 0

    def pushed_registers(self, insn):
        """The register a push stores (a list of one), None for a push of memory or an immediate."""
        if insn.mnemonic != "push" or not insn.operands:
            return []
        op = insn.operands[0]
        if op.type == X.X86_OP_REG:
            return [self._name(insn.reg_name(op.reg))]
        return [None]

    def literal(self, insn, isa):
        """None: i386 code reads its constants from immediates and absolute addresses."""
        return None

    def pc_address(self, insn, isa):
        return None

    def immediates(self, insn):
        return [o.imm & 0xFFFFFFFF for o in insn.operands if o.type == X.X86_OP_IMM]

    # ---- the overlay and the listing ----

    def start_address(self, address, isa):
        return address

    def isa_of_symbol(self, value):
        return "i386"

    @staticmethod
    def symbol_asm(name, address, isa):
        return "\t.globl %s\n\t.set %s, 0x%x\n\t.type %s, @function\n" % (name, name, address, name)

    def assembler_flags(self, compiler=None):
        """GNU as assembles 64-bit by default (--32); clang's driver has its target from the triple."""
        return [] if compiler is not None and compiler.kind == "clang" else ["--32"]

    def linker_flags(self, compiler=None):
        """GNU ld links 64-bit by default; lld takes the class from the objects."""
        return [] if compiler is not None and compiler.kind == "clang" else ["-m", "elf_i386"]

    def asm(self):
        return X86Asm(self)

    # ---- the emulator ----

    def uc_mode(self, isa=None):
        return UC_MODE_32

    def new_uc(self, isa=None):
        return Uc(UC_ARCH_X86, UC_MODE_32)

    def initial_status(self, engine):
        engine.reg_write(UX.UC_X86_REG_EFLAGS, EFLAGS_INIT)

    def status_reg(self):
        return UX.UC_X86_REG_EFLAGS

    def settle_it(self, engine):
        pass

    def flags_value(self, engine):
        return engine.reg_read(UX.UC_X86_REG_EFLAGS)

    def pc_reg(self):
        return UX.UC_X86_REG_EIP

    def push_return(self, engine, address, write):
        """Set up a call to a function as call does: the return address pushed (write(address, bytes))."""
        sp = (engine.reg_read(UX.UC_X86_REG_ESP) - 4) & 0xFFFFFFFF
        write(sp, struct.pack("<I", address & 0xFFFFFFFF))
        engine.reg_write(UX.UC_X86_REG_ESP, sp)

    def pop_return(self, engine, read):
        """Return from a stub as ret does: the return address popped (read(address, size) -> bytes); returns it."""
        sp = engine.reg_read(UX.UC_X86_REG_ESP)
        ret = struct.unpack("<I", bytes(read(sp, 4)))[0]
        engine.reg_write(UX.UC_X86_REG_ESP, (sp + 4) & 0xFFFFFFFF)
        return ret


class X86Asm:
    """How dread asm writes i386 code: capstone's AT&T text, which GNU as (--32) reads back; jumps and
    calls go to labels. What GNU as encodes otherwise (another length of a jump, another form of an
    operand) the verify loop writes as raw bytes."""

    comment = "#"
    directives = {"word": ".long", "half": ".short", "byte": ".byte", "text": ".asciz"}

    def __init__(self, arch):
        self.arch = arch
        self._att = cs.Cs(cs.CS_ARCH_X86, cs.CS_MODE_32)
        self._att.syntax = cs.CS_OPT_SYNTAX_ATT

    def prelude(self, isa):
        return ["\t.code32"]

    def switch(self, isa):
        return []

    def raw(self, b, isa):
        return ".byte", ", ".join("0x%02x" % x for x in b)

    def function_lines(self, label, isa, code):
        return ["\t.type   %s, @function" % label]

    def operands(self, listing, insn, run, st, isa):
        att = next(self._att.disasm(bytes(insn.bytes), insn.address, 1), None)
        if att is None:
            return None
        kind, target, _ = self.arch.flow(insn)
        if kind in ("jump", "call") and target is not None:
            tst = listing.prog.stored(target)
            if listing.space(target) == listing.space(run) and listing.has(tst, 1) and listing.labelable(tst):
                listing.labels.add(tst)
                return att.mnemonic, ("label", "", tst), ""
            return None
        return att.mnemonic, att.op_str, ""
