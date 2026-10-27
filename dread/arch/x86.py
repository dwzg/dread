"""The x86 layer: 32-bit x86 (i386) in flat protected mode, on capstone and Unicorn.

The registers are eax, ecx, edx, ebx, esp, ebp, esi and edi (a write to al, ah or ax is a write to
eax that keeps the rest of it, so it reads eax too), eip, and the six arithmetic flags of eflags as
"flags"; the analyses call esp "sp" and eip "pc", the names every layer gives its stack pointer and
program counter. A call pushes its return address (link_reg None: the emulator's push_return and pop_return,
as for MSP430). Instructions take 1 to 15 bytes (max_insn: the program database reads that many to
learn a length). The analysis decodes in Intel syntax (the destination first in capstone's operand
list); the listing writes AT&T syntax, which GNU as reads back (X86Asm), branches and calls to labels.

The calling convention is the System V i386 one (cdecl) by default: every argument on the stack
(default_args 0), the result in eax (edx:eax for 64 bits), ebx, esi, edi and ebp kept; a target's
[abi] may state another (args = 3 with eax, edx, ecx for GCC's -mregparm=3).

For dread.regflow the layer has the bits of a register's parts (bit_demand, merge_demand, flag_demand,
store_demand, refine_saved_writes): a byte loaded into dl reads none of the rest of edx unless the rest is
used. A stub finds its arguments behind the return address the call pushed (entry_stack_args).

Not yet: the lifter, interrupts (the IDT), the x87, MMX and SSE registers, string instructions beyond
what the analysis needs, 16-bit and 64-bit modes.
"""
import struct

import capstone as cs
from capstone import CS_AC_READ, CS_AC_WRITE
from capstone import x86 as X
from unicorn import UC_ARCH_X86, UC_MODE_32, Uc
from unicorn import x86_const as UX

from dread.arch import extend_bits, low_bits, sum_value, unshift

NAMES = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi"]
# the full register of each name capstone may report (the 8- and 16-bit parts of the eight)
FULL = {"al": "eax", "ah": "eax", "ax": "eax", "eax": "eax",
        "cl": "ecx", "ch": "ecx", "cx": "ecx", "ecx": "ecx",
        "dl": "edx", "dh": "edx", "dx": "edx", "edx": "edx",
        "bl": "ebx", "bh": "ebx", "bx": "ebx", "ebx": "ebx",
        "spl": "sp", "sp": "sp", "esp": "sp",
        "bpl": "ebp", "bp": "ebp", "ebp": "ebp",
        "sil": "esi", "si": "esi", "esi": "esi",
        "dil": "edi", "di": "edi", "edi": "edi",
        "eflags": "flags", "flags": "flags", "eip": "pc", "ip": "pc"}
PARTS = {name for name, full in FULL.items() if full in NAMES + ["sp"] and name not in (full, "esp")}
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
M32 = 0xFFFFFFFF
# each register name capstone reports: (the analyses' name of its whole register, the bits it is of
# that register, how far up they lie)
LANES = {}
for _full, (_low, _high, _word) in {"eax": ("al", "ah", "ax"), "ecx": ("cl", "ch", "cx"),
                                    "edx": ("dl", "dh", "dx"), "ebx": ("bl", "bh", "bx")}.items():
    LANES.update({_low: (_full, 0xFF, 0), _high: (_full, 0xFF00, 8), _word: (_full, 0xFFFF, 0), _full: (_full, M32, 0)})
for _full, _word, _byte in (("esi", "si", "sil"), ("edi", "di", "dil"), ("ebp", "bp", "bpl")):
    LANES.update({_byte: (_full, 0xFF, 0), _word: (_full, 0xFFFF, 0), _full: (_full, M32, 0)})
LANES.update({"esp": ("sp", M32, 0)})


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
    port_instructions = (UX.UC_X86_INS_IN, UX.UC_X86_INS_OUT)    # in and out: dread.emu's port space
    COUNTER_READ = b"\x0f\x31"              # rdtsc: dread.emu answers it (counter_sites, answer_counter)
    folds_memory_operands = True            # an instruction may read memory beside a register: a compiler may
                                            # fold two volatile loads into one and reorder them (GCC 16;
                                            # dread.lift_x86's barrier, dread.volorder in readable C)
    pushes_arguments = True                 # cdecl pushes a call's stack arguments (dread.regflow: such a
                                            # push reads its register)
    entry_stack_args = 4                    # sp to the first stack argument at a function's entry: the
                                            # return address the call pushed lies between
    veneer_reg = "eax"                      # no veneers (a 5-byte jmp reaches every address); eax is where
                                            # GCC and clang mostly load a pointer they call through
    generator_version = 8           # 2: a case gives eax, edx and ecx to a function that takes them (case_regs);
                                    # 3: one that only pushes edx or ecx as a call's argument takes them;
                                    # 4: a case answers the ports its runs read (ports); 5: padding reads
                                    # no register (is_padding: regflow's reads, case_regs); 6: a frame
                                    # aligned at run time has depths (lift_x86 realigns: the stack
                                    # arguments of such a function and its stubs' words are known);
                                    # 7: rdtsc reads dread.emu's counter, not the host's clock;
                                    # 8: the compare-guided stage sees x86's branches (value_step,
                                    # store_slots, load_address, memory operands in branch_deciders)
    refine_saved_writes = True              # dread.regflow refines a write of ebx, esi, edi or ebp too (taken as
                                            # all used): with three scratch registers, bytes pass through them
    SWITCH_MAX = 1024                       # table entries read at most
    HIGH_BYTES = ("ah", "bh", "ch", "dh")

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
        """The general registers a function may use for values (not esp: "sp", the tools' name for the
        stack pointer on every layer)."""
        return [n for n in NAMES if n != "esp"]

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

    UNLIKELY = frozenset(("in", "out", "insb", "insw", "insd", "outsb", "outsw", "outsd", "cli", "sti", "hlt",
                          "lgdt", "lidt", "lldt", "ltr", "arpl", "bound", "into", "das", "daa", "aas", "aaa",
                          "aam", "aad", "salc"))

    stub_regs = ("eax", "edx", "ecx")      # what a stub may log of a callee's registers (dread.autocase's
                                            # reads_mask: those of them it reads; the arguments a stub gets
                                            # are stack words, cdecl's)

    def case_regs(self, reads):
        """The registers a generated case gives a function's first arguments in (dread.autocase.case_regs),
        from what it reads at its entry (dread.regflow): eax, edx and ecx where it reads edx or ecx (GCC's
        local regparm(3), LLVM's fastcc), eax where it reads eax alone (one argument there, or the caller's
        eax handed back), else None (cdecl: the stack alone)."""
        if reads & {"edx", "ecx"}:
            return ["eax", "edx", "ecx"]
        return ["eax"] if "eax" in reads else None

    def unlikely_unreferenced(self, insn):
        """Whether an instruction is unlikely in code that nothing calls and that runs on into the next
        label (dread.elfimport.data_labels): port I/O, interrupt masking, hlt, the descriptor tables,
        BCD adjustments; FreeRTOS's IA32 multiboot header, read as code, has sti and in al, 4."""
        return insn.mnemonic in self.UNLIKELY

    def is_syscall(self, insn):
        return insn.mnemonic in ("int", "into", "syscall", "sysenter")

    def software_interrupt(self, engine, intno):
        """Whether an interrupt the emulator stopped for is int N (a system call: dread.emu runs it as a
        logged no-op, as ARM's svc and RISC-V's ecall): Unicorn reports it with N, as it reports a fault
        with its vector (a division by zero as 0), but resumes behind the instruction, whose two bytes
        cd NN lie in front of eip. int3 (cc) and into (ce) are no system calls."""
        eip = engine.reg_read(UX.UC_X86_REG_EIP)
        try:
            return 3 < intno < 256 and bytes(engine.mem_read((eip - 2) & 0xFFFFFFFF, 2)) == bytes([0xCD, intno])
        except Exception:
            return False

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

    def is_padding(self, insn):
        """Whether an instruction is one of the no-ops assemblers pad with: nop of any length (90, 66 90,
        0f 1f with any operand and prefixes), and, for cores without the long nop (GNU as for -march=pentium,
        FreeRTOS's IA32 demo), a lea of a 32-bit register to itself with displacement 0 (lea esi, [esi],
        any encoding, a segment prefix included: lea computes no segment's address) and a mov or xchg of a
        register to itself. They read and write nothing (reg_effects), the lifter writes no C for them, and
        dread.lift takes them for padding where no path reaches them (GCC -O2's alignment of loop heads:
        refused as "code no path from its entry reaches" until 2026-10-27)."""
        m, ops = insn.mnemonic, insn.operands
        if m == "nop":
            return True
        if len(ops) != 2:
            return False
        a, b = ops
        if a.type != X.X86_OP_REG:
            return False
        lane = LANES.get(insn.reg_name(a.reg))
        if lane is None or lane[0] == "sp":
            return False
        if m in ("mov", "xchg") and b.type == X.X86_OP_REG and b.reg == a.reg:
            return lane[1] == M32 or m == "xchg"
        if m == "lea" and b.type == X.X86_OP_MEM:
            mem = b.mem
            return lane[1] == M32 and mem.base == a.reg and not mem.index and mem.disp == 0
        return False

    def reg_effects(self, insn):
        """(reads, writes): the registers an instruction reads and writes by name (the eight, "flags",
        "pc"), from capstone's lists with the implicit ones (push and pop: esp; mul and div: eax, edx;
        call and ret: esp). A write to a register's part (al, ax) keeps the rest: it reads the whole
        register too."""
        if self.is_padding(insn):
            return set(), set()             # (a no-op: lea esi, [esi] reads no esi of its caller's)
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
                all(o.type == X.X86_OP_REG for o in insn.operands) and insn.operands[0].reg == insn.operands[1].reg \
                and LANES.get(insn.reg_name(insn.operands[0].reg), (None, 0))[1] == M32:
            # (xor eax, eax: 0; sbb: the carry. xor al, al keeps the rest of eax: a read of it)
            reads.discard(self._name(insn.reg_name(insn.operands[0].reg)))
        if insn.mnemonic.startswith("cmov") and insn.operands and insn.operands[0].type == X.X86_OP_REG:
            reads.add(self._name(insn.reg_name(insn.operands[0].reg)))     # (kept where the condition fails)
        if insn.mnemonic in ("cdq", "cwd"):
            writes.discard("eax")           # (capstone 5 lists eax: they write edx alone, the sign of eax)
        m = insn.mnemonic.split()[-1]       # (rep movsd: capstone's mnemonic carries the prefix)
        if m[:4] in ("movs", "stos", "lods", "scas", "cmps", "outs") or m[:3] == "ins" and len(m) == 4:
            # a string instruction reads the direction flag alone, which the ABI keeps clear (cld is a
            # nop to the lifter, std refused): no read of the flags a call left. Counted, regflow took
            # the flags that FreeRTOS's inl left (its xor eax, eax) for a result: a rep movsl two calls
            # up read them, through pciIOread32 and outl, which write none
            reads.discard("flags")
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
        """The immediates of an instruction, as candidate input values (none of jumps and calls, whose
        immediate is their target)."""
        if self.flow(insn)[0] in ("jump", "call"):
            return []
        return [o.imm & 0xFFFFFFFF for o in insn.operands if o.type == X.X86_OP_IMM]

    # ---- switches (dread.switches) ----

    def _reg_operand(self, insn, op):
        """The full register of a register operand, None for another operand or a high byte (ah: the
        index would be bits 8 to 15 of its register)."""
        if op.type != X.X86_OP_REG:
            return None
        name = insn.reg_name(op.reg)
        return None if name in self.HIGH_BYTES else FULL.get(name)

    def bound_check(self, insn, prior=(), known=None):
        """(register, limit, before) when the instruction bounds a switch index: cmp r, imm (at most
        imm + 1 values pass, the ja behind it leaves for the rest; set before the compare: GCC's cmp
        eax, 6; ja in front of its jump through the table), or and r, 2^n - 1 (r takes 0 .. 2^n - 1;
        set after the and: clang's and eax, 7 in front of a jump through an eight-entry table that no
        compare guards). A byte or word register stands for its whole register. None otherwise: a
        compare of memory (the index is loaded behind it), a bound held in a register (known is not
        used: the compare sits in front of the jump, which a value set there would not change)."""
        ops = insn.operands
        if insn.mnemonic not in ("cmp", "and") or len(ops) != 2 or ops[1].type != X.X86_OP_IMM:
            return None
        reg = self._reg_operand(insn, ops[0])
        imm = ops[1].imm & 0xFFFFFFFF
        if reg is None or reg in ("esp", "ebp"):
            return None
        if insn.mnemonic == "cmp" and imm < 0x10000:
            return reg, imm, True
        if insn.mnemonic == "and" and 0 < imm < 0x10000 and imm & (imm + 1) == 0:
            return reg, imm, False
        return None

    def switch_targets(self, insn, program, inside):
        """The targets of jmp dword ptr [rN*4 + table] (no base register: the table's address is in
        the instruction), read while its entries lie in the function (inside(address)), at most
        SWITCH_MAX; None for any other instruction. dread.switches runs the dispatch first; this is
        its fallback where the run finds no target (a table jump without a bound check it knows)."""
        from dread.target import TargetError
        if insn.mnemonic != "jmp" or len(insn.operands) != 1:
            return None
        op = insn.operands[0]
        if op.type != X.X86_OP_MEM or op.mem.base != 0 or op.mem.index == 0 or op.mem.scale != 4 \
                or op.mem.segment != 0:
            return None
        table, targets = op.mem.disp & 0xFFFFFFFF, []
        for k in range(self.SWITCH_MAX):
            try:
                word = struct.unpack("<I", bytes(program.read(table + 4 * k, 4)))[0]
            except TargetError:
                break
            if not inside(word):
                break
            targets.append(word)
        return targets

    # ---- what the case generator learns from the code (dread.autocase, dread.record) ----

    def counter_sites(self, data, base):
        """The addresses in data (loaded at base) where an rdtsc would start: every 0f 31. dread.emu hooks
        each one (a code hook fires only where an instruction starts, and an instruction that starts with
        0f 31 is rdtsc, so the bytes of 0f 31 inside other instructions or data cost a hook and nothing
        else)."""
        out, at = [], data.find(self.COUNTER_READ)
        while at >= 0:
            out.append(base + at)
            at = data.find(self.COUNTER_READ, at + 1)
        return out

    def answer_counter(self, uc, address, value):
        """rdtsc's result: the counter's 64 bits in edx and eax, and on behind the instruction (a code hook
        that moves eip runs no rdtsc, whose answer is the host's clock in Unicorn)."""
        uc.reg_write(UX.UC_X86_REG_EAX, value & 0xFFFFFFFF)
        uc.reg_write(UX.UC_X86_REG_EDX, (value >> 32) & 0xFFFFFFFF)
        uc.reg_write(UX.UC_X86_REG_EIP, address + len(self.COUNTER_READ))

    def prologue_frame(self, program, fn, look=16):
        """The stack a function's prologue reserves for locals: what sub esp, N lowers esp by before
        the first branch or call (the pushes of the registers it keeps are no locals: push ebp; mov
        ebp, esp; push esi; push ebx; sub esp, 0x10 reserves 16)."""
        total = 0
        for a in fn.insns[:look]:
            insn = program.insn(a)
            if insn is None or self.flow(insn)[0] != "next":
                break
            ops = insn.operands
            if insn.mnemonic == "sub" and len(ops) == 2 and self._reg_operand(insn, ops[0]) == "esp" \
                    and insn.reg_name(ops[0].reg) == "esp" and ops[1].type == X.X86_OP_IMM:
                total += ops[1].imm & 0xFFFFFFFF
        return total

    def switch_hints(self, insns):
        """(bounds, table): the small constants compares check (cmp r, k), and whether the code jumps
        through a register or a table (jmp eax, jmp dword ptr [eax*4 + table])."""
        limits, table = set(), False
        for ins in insns:
            ops = ins.operands
            if ins.mnemonic == "cmp" and len(ops) == 2 and ops[1].type == X.X86_OP_IMM and 0 < ops[1].imm <= 0xFF:
                limits.add(ops[1].imm)
            if ins.mnemonic == "jmp" and ops and ops[0].type != X.X86_OP_IMM:
                table = True
        return limits, table

    def compare_chars(self, insn):
        """Values an instruction compares against that may be characters: cmp with an immediate, of a
        register or of memory (x86 compares a byte in memory directly: cmp byte ptr [ecx], 0x2c)."""
        ops = insn.operands
        if insn.mnemonic == "cmp" and len(ops) == 2 and ops[1].type == X.X86_OP_IMM:
            return [ops[1].imm & 0xFFFFFFFF]
        return []

    TESTS = ("test", "and")                 # the flags of a & b: "test"
    DECIDERS = ("cmp", "test", "sub", "and", "xor", "add")    # (xor: whether a equals b; add: whether a is -b)

    def branch_deciders(self, insns):
        """{conditional jump: (address of what decides it, first, second, imm, kind, sign)} as
        Arm.branch_deciders documents: the flag setter in front of the jump (cmp, test and and: "test";
        cmp, sub and xor: "compare"; add: a compare with the negation), when its operands are
        registers or a register and a constant, at most 16 bytes before with nothing between that sets
        the flags or leaves the straight run (a mov passes). Byte and word registers are read whole:
        exact where the compiler extended the byte into the register first (movzx), a hint otherwise.
        An operand in memory (cmp dword ptr [ebp - 0x28], 0; cmp byte ptr [ecx], 0x2c; cmp eax, [ebx +
        4]) is ("mem", base, index, scale, displacement, size), engine ids or None: the generator's
        reader reads the memory there (Generator.flag_setters). Until 2026-10-27 a compare with memory
        gave none, and the compare-guided stage had nothing to solve at most of x86's branches."""
        index = {ins.address: k for k, ins in enumerate(insns)}
        out = {}
        for ins in insns:
            if ins.mnemonic not in JUMPS or ins.mnemonic in ("jecxz", "jcxz", "loop", "loope", "loopne"):
                continue
            k = index[ins.address] - 1
            while k >= 0 and ins.address - insns[k].address <= 16:
                s = insns[k]
                sets = self.sets_flags(s)
                if not sets and self.flow(s)[0] == "next":
                    k -= 1                  # (it leaves the flags: look further back)
                    continue
                ops = s.operands
                if sets and s.mnemonic in self.DECIDERS and len(ops) == 2 and ops[0].type != X.X86_OP_IMM \
                        and ops[1].type in (X.X86_OP_REG, X.X86_OP_IMM, X.X86_OP_MEM) \
                        and not (ops[0].type == X.X86_OP_MEM and ops[1].type == X.X86_OP_MEM):
                    first = self._decider_operand(s, ops[0])
                    second = self._decider_operand(s, ops[1]) if ops[1].type != X.X86_OP_IMM else None
                    if first is not None and (second is not None or ops[1].type == X.X86_OP_IMM):
                        imm = ops[1].imm & 0xFFFFFFFF if ops[1].type == X.X86_OP_IMM else 0
                        kind = "test" if s.mnemonic in self.TESTS else "compare"
                        sign = -1 if s.mnemonic == "add" else 1
                        out[ins.address] = (s.address, first, second, imm, kind, sign)
                break
        return out

    def _decider_operand(self, insn, op):
        """A decider's operand: the engine's id of a whole register, or ("mem", base, index, scale,
        displacement, size) of a memory operand without a segment (ids or None); None for a high byte."""
        if op.type == X.X86_OP_REG:
            reg = self._reg_operand(insn, op)
            return self.reg_id(reg) if reg is not None else None
        if op.type != X.X86_OP_MEM or op.mem.segment:
            return None
        base = self.reg_id(self._name(insn.reg_name(op.mem.base))) if op.mem.base else None
        index = self.reg_id(self._name(insn.reg_name(op.mem.index))) if op.mem.index else None
        return ("mem", base, index, op.mem.scale, op.mem.disp, op.size)

    # ---- what dread.uncovered, dread.relations and dread.statics learn of the values ----

    absolute_accesses = True        # an access value_step gives at base 0 lies at a fixed address (dread.uncovered)
    STRING = ("movs", "stos", "lods", "scas", "cmps")

    def address_regs(self, insn):
        """The whole registers an instruction's memory operands compute their addresses from (esp: "sp"):
        dread.uncovered tells them from the values the instruction reads besides (cmp [ebx + 4], edx)."""
        return self._address_regs(insn)

    def _memory_operands(self, insn):
        """[(operand, base name or None, index name or None)] of the explicit memory operands that access
        memory: none of lea, nop, string instructions or a segment's (fs:)."""
        m = insn.mnemonic.split()[-1]
        if m in ("lea", "nop") or m[:4] in self.STRING:
            return []
        out = []
        for op in insn.operands:
            if op.type == X.X86_OP_MEM and not op.mem.segment:
                out.append((op, self._name(insn.reg_name(op.mem.base)) if op.mem.base else None,
                            self._name(insn.reg_name(op.mem.index)) if op.mem.index else None))
        return out

    def store_slots(self, insn):
        """[(register, base register, displacement)] of a mov of a register into memory at a register plus
        a constant (mov dword ptr [ebp - 0x1c], eax; mov byte ptr [esp + 3], dl: the whole register's
        name), else [] (dread.regflow Frames: what a slot of the frame holds; a push is the stack's)."""
        if insn.mnemonic != "mov" or len(insn.operands) != 2:
            return []
        dst, src = insn.operands
        if dst.type != X.X86_OP_MEM or src.type != X.X86_OP_REG or dst.mem.segment or not dst.mem.base \
                or dst.mem.index:
            return []
        reg = LANES.get(insn.reg_name(src.reg))
        if reg is None:
            return []
        return [(reg[0], self._name(insn.reg_name(dst.mem.base)), dst.mem.disp)]

    def load_address(self, insn):
        """(base register, displacement) of a load from a register plus a constant, any width (dread.uncovered
        frame_load: the frame's slots, at esp or at a frame pointer), else None."""
        mems = self._memory_operands(insn)
        if len(mems) != 1:
            return None
        op, base, index = mems[0]
        if base is None or index is not None or not op.access & CS_AC_READ:
            return None
        return base, op.mem.disp

    def value_step(self, insn, isa, known, program):
        """What an instruction does with the constants registers hold, known: {name: (value, indexed)}
        (dread.uncovered, dread.relations, dread.statics; the ARM layer's says what it returns): mov of an
        immediate or a register, lea, add and sub of an immediate or a register and xor of a register with
        itself give values (None where an input is not known: asked again with constants); each memory
        operand at a known base plus its displacement is an access, at a fixed address with base 0
        (absolute_accesses), indexed where an index register takes part, none at esp (frame_load has the
        frame); a mov into memory stores its register."""
        values, accesses, stored = {}, [], []
        m, ops = insn.mnemonic, insn.operands

        def whole(op):
            lane = LANES.get(insn.reg_name(op.reg)) if op.type == X.X86_OP_REG else None
            return lane[0] if lane is not None and lane[1] == M32 else None
        dst = whole(ops[0]) if len(ops) == 2 else None
        if dst is not None and dst != "sp":
            src = ops[1]
            if m == "mov" and src.type == X.X86_OP_IMM:
                values[dst] = (src.imm & M32, False)
            elif m == "mov" and src.type == X.X86_OP_REG:
                values[dst] = known.get(whole(src)) if whole(src) is not None else None
            elif m == "lea":
                base = self._name(insn.reg_name(src.mem.base)) if src.mem.base else None
                b = known.get(base) if base is not None else (0, False)
                if b is not None and base != "sp":
                    v = sum_value(b, (src.mem.disp & M32, False))
                    values[dst] = (v[0], True) if src.mem.index else v
                else:
                    values[dst] = None
            elif m in ("add", "sub") and src.type in (X.X86_OP_IMM, X.X86_OP_REG):
                b = (src.imm & M32, False) if src.type == X.X86_OP_IMM else \
                    (known.get(whole(src)) if whole(src) is not None else None)
                values[dst] = sum_value(known.get(dst), b, m == "add")
            elif m == "xor" and src.type == X.X86_OP_REG and src.reg == ops[0].reg:
                values[dst] = (0, False)
            elif m in ("mov", "add", "sub", "and", "or", "xor", "imul", "movzx", "movsx", "adc", "sbb") \
                    and src.type != X.X86_OP_MEM:
                values[dst] = None          # (computed from what is not known; a load gives none, as RISC-V's)
        for op, base, index in self._memory_operands(insn):
            if base == "sp":
                continue
            b = known.get(base) if base is not None else (0, False)
            if b is None:
                continue
            load, store = bool(op.access & CS_AC_READ), bool(op.access & CS_AC_WRITE)
            if m == "push":
                load, store = True, False
            elif m == "pop":
                load, store = False, True
            signed = m == "movsx" and op is ops[-1]
            ctype = ("s" if signed else "u") + str(8 * op.size)
            accesses.append((b[0], op.mem.disp, ctype, b[1] or index is not None, load, store))
            if store and m == "mov" and ops[1].type == X.X86_OP_REG and whole(ops[1]) is not None:
                stored.append(whole(ops[1]))
        return values, accesses, stored

    # ---- what dread.regflow learns of the bits ----

    REGISTER_OPS = frozenset(("mov", "movzx", "movsx", "add", "sub", "and", "or", "xor", "adc", "sbb", "inc", "dec",
                              "neg", "not", "shl", "sal", "shr", "sar", "rol", "ror", "lea", "imul", "mul", "cmp",
                              "test", "bswap", "xchg", "cdq", "cwd", "cwde", "cbw"))
    READS_DESTINATION = ("add", "sub", "and", "or", "xor", "adc", "sbb")

    def _lane(self, insn, op):
        """(whole register, its bits, their shift) of a register operand, None for another operand."""
        if op.type != X.X86_OP_REG:
            return None
        return LANES.get(insn.reg_name(op.reg))

    def _address_regs(self, insn):
        """The whole registers a memory operand of the instruction computes its address from."""
        out = set()
        for o in insn.operands:
            if o.type == X.X86_OP_MEM:
                for r in (o.mem.base, o.mem.index):
                    if r:
                        out.add(FULL.get(insn.reg_name(r), insn.reg_name(r)))
        return out

    def register_only(self, insn):
        """Whether the instruction only computes registers from registers and immediates (dread.regflow:
        one whose results nobody uses reads nothing): the moves, arithmetic and logic, shifts, lea (an
        address computed, nothing accessed), setcc and cmovcc; not with a memory operand, not esp's
        (push, pop, sub esp: the stack), not div (it traps on 0)."""
        m = insn.mnemonic
        if m not in self.REGISTER_OPS and not m.startswith(("set", "cmov")):
            return False
        if m != "lea" and any(o.type == X.X86_OP_MEM for o in insn.operands):
            return False
        writes = self.reg_effects(insn)[1]
        return bool(writes) and all(w == "flags" or (w in NAMES and w != "esp") for w in writes)

    def bit_demand(self, insn, out):
        """{register: bits} of the registers an instruction that only computes registers (register_only)
        reads, the bits of each that bits `out` ({register: bits} of what it writes) of its result depend
        on; None where this layer has no rule (dread.regflow then takes every bit of every register it
        reads once any bit of the result is used). A write to part of a register (al, ah, ax) keeps the
        rest, which it reads where that is used. Rules: mov, movzx and movsx (the bits moved, the sign
        bit where a bit above is used), and, or, xor (the same bits; with an immediate where it lets them
        through), add, sub, inc, dec, neg, imul (the bits up to the highest used: a carry goes upwards),
        not, shl, sal, shr, sar, rol and ror by a constant (32-bit: the bits the shift moves there) and
        by cl (its low five bits; of the operand, its bits up to the highest used for shl, else all), lea
        (its base up to the highest bit used, its index as far as the scale leaves it), setcc and cmovcc
        (the flags), bswap, xchg, cdq, cwd, cwde and cbw; any other write to part of a register: every
        bit it reads where the written part is used, else the kept bits alone. Not adc, sbb, mul, and the
        8- and 16-bit shifts. Checked against the emulator on every rule
        (tests/test_regflow_bits.py)."""
        m, ops = insn.mnemonic, insn.operands
        reads, writes = self.reg_effects(insn)
        outs = [w for w in writes if w != "flags"]
        need = {}

        def add(reg, bits):
            if reg is not None and bits & M32:
                need[reg] = need.get(reg, 0) | (bits & M32)

        def source(op, bits):               # bits of an operand's value: of its register, where they lie
            lane = self._lane(insn, op)
            if lane is not None:
                add(lane[0], (bits << lane[2]) & lane[1])
        if m in ("cdq", "cwd"):             # edx (dx) takes copies of the sign of eax (ax)
            d = out.get("edx", 0) & (M32 if m == "cdq" else 0xFFFF)
            add("eax", (1 << (31 if m == "cdq" else 15)) if d else 0)
            add("edx", out.get("edx", 0) & ~0xFFFF if m == "cwd" else 0)
            return need
        if m in ("cwde", "cbw"):            # eax from ax, ax from al, sign-extended
            d = out.get("eax", 0)
            add("eax", extend_bits(d, 16, True) if m == "cwde" else extend_bits(d & 0xFFFF, 8, True) | (d & ~0xFFFF))
            return need
        if m == "xchg" and len(ops) == 2 and all(self._lane(insn, o) and self._lane(insn, o)[1] == M32 for o in ops):
            a, b = self._lane(insn, ops[0])[0], self._lane(insn, ops[1])[0]
            add(b, out.get(a, 0))
            add(a, out.get(b, 0))
            return need
        if len(outs) != 1 or not ops or self._lane(insn, ops[0]) is None or m in ("cmp", "test"):
            return None
        full, lane, shift = self._lane(insn, ops[0])
        if full != outs[0]:
            return None
        d = out.get(full, 0) & M32
        add(full, d & ~lane)                # the bits the write keeps
        v = (d & lane) >> shift             # the used bits of the part written, as a value
        width = 32 if lane == M32 else 16 if lane == 0xFFFF else 8
        if not v:
            return need
        imm = ops[-1].imm & ((1 << width) - 1) if ops[-1].type == X.X86_OP_IMM else None
        if m in ("xor", "sub") and len(ops) == 2 and ops[1].type == X.X86_OP_REG and ops[0].reg == ops[1].reg:
            return need                     # (xor al, al: the part is 0)
        if m == "mov":
            source(ops[1], v)
        elif m in ("movzx", "movsx"):
            lane2 = self._lane(insn, ops[1])
            source(ops[1], extend_bits(v, 16 if lane2[1] == 0xFFFF else 8, m == "movsx"))
        elif m in ("and", "or", "xor"):
            if imm is not None:
                source(ops[0], v & imm if m == "and" else v & ~imm if m == "or" else v)
            else:
                source(ops[0], v)
                source(ops[1], v)
        elif m in ("add", "sub"):
            source(ops[0], low_bits(v))
            source(ops[1], low_bits(v))
        elif m in ("inc", "dec", "neg"):
            source(ops[0], low_bits(v))
        elif m == "not":
            source(ops[0], v)
        elif m == "imul" and len(ops) >= 2:
            for o in (ops[:2] if len(ops) == 2 else ops[1:2]):
                source(o, low_bits(v))
        elif m in ("shl", "sal", "shr", "sar", "rol", "ror") and width == 32 and imm is not None:
            k = imm & 31
            kind = {"shl": "lsl", "sal": "lsl", "shr": "lsr", "sar": "asr", "ror": "ror", "rol": "ror"}[m]
            source(ops[0], unshift(v, kind, (32 - k) % 32 if m == "rol" else k))
        elif m in ("shl", "sal", "shr", "sar", "rol", "ror") and width == 32 and len(ops) == 2 \
                and ops[1].type == X.X86_OP_REG and insn.reg_name(ops[1].reg) == "cl":
            source(ops[0], low_bits(v) if m in ("shl", "sal") else M32)    # (by an unknown count)
            add("ecx", 0x1F)                # the count: cl's low five bits
        elif m == "lea":
            mem, low = ops[1].mem, low_bits(v)
            if mem.base:
                add(FULL.get(insn.reg_name(mem.base)), low)
            if mem.index:
                add(FULL.get(insn.reg_name(mem.index)), low >> (mem.scale.bit_length() - 1))
        elif m.startswith("set"):
            add("flags", M32 if v & 1 else 0)
        elif m.startswith("cmov"):
            source(ops[0], v)
            source(ops[1], v)
            add("flags", M32)
        elif m == "bswap":
            source(ops[0], int.from_bytes(v.to_bytes(4, "little"), "big"))
        elif lane != M32 and m not in ("adc", "sbb"):
            for r in reads:                 # any other write of a part: what it reads, wholly
                add(r, M32)
        else:
            return None
        return need

    def merge_only(self, insn):
        """The whole registers an instruction reads only to keep the bits outside the part it writes
        (mov bl, cl; mov dl, byte ptr [eax]; setne al; xor al, al): a part as its destination, the
        register read in no other way (no source, no address). dread.lift_x86's entry_states."""
        ops, m = insn.operands, insn.mnemonic
        if not ops or ops[0].type != X.X86_OP_REG:
            return frozenset()
        lane = LANES.get(insn.reg_name(ops[0].reg))
        if lane is None or lane[1] == M32:
            return frozenset()
        zero = m in ("xor", "sub") and len(ops) == 2 and ops[1].type == X.X86_OP_REG and ops[0].reg == ops[1].reg
        if not (zero or m in ("mov", "movzx", "movsx", "lea") or m.startswith("set")):
            return frozenset()
        if lane[0] in self._address_regs(insn):
            return frozenset()
        if not zero and any((self._lane(insn, o) or (None,))[0] == lane[0] for o in ops[1:]):
            return frozenset()
        return frozenset((lane[0],))

    def flag_demand(self, insn):
        """{register: bits} of what the flags an instruction that only computes registers sets depend on:
        an 8-, 16- or 32-bit operation's flags come from its operands' parts (cmp dl, 0x20: the low byte
        of edx), the flags it reads (adc, sbb) and what it reads besides its operands (mul: eax), wholly.
        Checked against the emulator (tests/test_regflow_bits.py)."""
        need = {}
        named = set()
        for o in insn.operands:
            lane = self._lane(insn, o)
            if lane is not None:
                need[lane[0]] = need.get(lane[0], 0) | lane[1]
                named.add(lane[0])
            elif o.type == X.X86_OP_MEM:
                return None
        for r in self.reg_effects(insn)[0]:
            if r not in named:              # (implicit: mul's eax, adc's flags)
                need[r] = M32
        return need

    def merge_demand(self, insn):
        """{register: (kept, operand)} of an instruction outside register_only that reads or writes parts
        of registers: the bits a write of a part keeps, which dread.regflow counts as read only where
        they are used behind it, and those it reads of each part (always read). mov dl, byte ptr [eax]
        keeps the upper 24 bits of edx and reads none of the rest; add dl, byte ptr [ecx] reads its low
        byte too; cmp al, byte ptr [ecx] reads the low byte of eax alone. None where it touches no part,
        and a register also read whole (an address, a whole operand, an implicit operand) is left out."""
        if self.register_only(insn):
            return None
        ops, m = insn.operands, insn.mnemonic
        writes_first = m not in ("cmp", "test", "push") and not m.startswith("j") and m != "call"
        reads_first = m not in ("mov", "movzx", "movsx", "lea", "pop") and not m.startswith("set")
        out, whole = {}, set(self._address_regs(insn))
        for k, o in enumerate(ops):
            lane = self._lane(insn, o)
            if lane is None:
                continue
            full, mask, _ = lane
            if mask == M32:
                whole.add(full)
                continue
            kept, operand = out.get(full, (0, 0))
            first = k == 0 and len(ops) >= 1
            if first and writes_first:
                kept |= ~mask & M32
            if not first or reads_first:
                operand |= mask
            out[full] = (kept, operand & ~kept & M32)
        named = set(out) | whole
        whole |= set(self.reg_effects(insn)[0]) - named - {"flags", "sp"}   # (implicit: none of their parts)
        for r in whole:
            out.pop(r, None)
        return out or None

    def store_demand(self, insn):
        """{register: bits} of what a byte's or a halfword's store takes of its register (mov byte ptr
        [eax], dl: the low byte; dh: the second), for dread.regflow; None for any other instruction,
        and where that register is also the address."""
        ops = insn.operands
        if insn.mnemonic != "mov" or len(ops) != 2 or ops[0].type != X.X86_OP_MEM:
            return None
        lane = self._lane(insn, ops[1])
        if lane is None or lane[1] == M32 or lane[0] in self._address_regs(insn):
            return None
        return {lane[0]: lane[1]}

    # ---- the overlay and the listing ----

    def start_address(self, address, isa):
        return address

    def branch(self, src, dst, isa, link=False):
        """jmp rel32 (call rel32 with link) at src to dst, 5 bytes: it reaches every address (the
        displacement wraps around the 4 GiB)."""
        return bytes([0xE8 if link else 0xE9]) + struct.pack("<I", (dst - (src + 5)) & 0xFFFFFFFF)

    def branch_length(self, isa, src=None, span=None):
        return 5

    def entry_branch(self, src, dst, isa, room=None):
        return self.branch(src, dst, isa)

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
        engine.reg_write(UX.UC_X86_REG_EIP, ret)
        engine.reg_write(UX.UC_X86_REG_ESP, (sp + 4) & 0xFFFFFFFF)
        return ret

    def resume_address(self, engine):
        """Where to go on after emu_start returned: eip."""
        return engine.reg_read(UX.UC_X86_REG_EIP)


class X86Asm:
    """How dread asm writes i386 code: capstone's AT&T text, which GNU as (--32) reads back; jumps and
    calls go to labels. What GNU as encodes otherwise (another length of a jump, another form of an
    operand) the verify loop writes as raw bytes."""

    comment = "#"
    directives = {"word": ".long", "half": ".short", "byte": ".byte", "text": ".asciz"}
    precheck = True                     # (dread.mkasm.precheck: a nop with a prefix, an explicit zero
                                        # displacement, which GNU as writes shorter)

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
