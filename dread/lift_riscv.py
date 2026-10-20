"""dread lift for RISC-V (RV32IMC): one C statement per instruction, as dread.lift does for ARM.

Registers are u32 variables with their ABI names (zero reads as 0, a write to it is dropped); loads
and stores are volatile accesses in the original order; branches are gotos. The stack pointer's depth
is known statically at every instruction (dread.lift's states), so an access at sp, or at a register
that holds a frame address, is one into the frame array; a store of a register into the frame before
anything wrote it is a save (the C keeps the caller's registers itself). A call passes a0-a7 and
takes a0 (a0 and a1 when the callers read a 64-bit result); the other scratch registers are 0 behind
it (a function that reads one there is refused, as the callee's value is not known). auipc and the
auipc/lui pairs become the constants they make at the original's address. The M extension's division
keeps RISC-V's results for a zero divisor (all ones; the remainder is the dividend) and for the one
overflow (-2^31 / -1). CSR instructions, fence and wfi run as themselves in inline assembly. A
function whose returns are mret is a machine-mode trap handler: clang's interrupt("machine") writes
its entry and its return.

Refused, with the reason: a write of sp that is not a frame adjustment the analysis follows (the
startup's la sp, ...), an access above the entry's stack pointer (stack arguments), ebreak,
gp or tp read before written, and what dread.lift refuses for every architecture (a stack pointer
that differs between paths, a fall-through out of the function, a register read before written).
"""
from dread import lift
from dread.arch.riscv import NAMES, fields
from dread.lift import IDENT, Refused

REGS = [n for n in NAMES if n != "zero"] + ["pc"]
ARGS = tuple("a%d" % i for i in range(8))
SCRATCH = ("t0", "t1", "t2", "t3", "t4", "t5", "t6") + ARGS
AFTER_CALL = set(SCRATCH) | {"ra"}
CONDS = {"beq": "%s == %s", "bne": "%s != %s", "blt": "(s32)%s < (s32)%s", "bge": "(s32)%s >= (s32)%s",
         "bltu": "%s < %s", "bgeu": "%s >= %s"}
LOADS = {"lb": "(u32)(s32)*(volatile s8 *)(%s)", "lbu": "(u32)*(volatile u8 *)(%s)",
         "lh": "(u32)(s32)*(volatile s16 *)(%s)", "lhu": "(u32)*(volatile u16 *)(%s)", "lw": "*(volatile u32 *)(%s)"}
STORES = {"sb": "u8", "sh": "u16", "sw": "u32"}
ALU_IMM = {"addi": "%s + 0x%xu", "xori": "%s ^ 0x%xu", "ori": "%s | 0x%xu", "andi": "%s & 0x%xu"}
ALU = {"add": "%s + %s", "sub": "%s - %s", "xor": "%s ^ %s", "or": "%s | %s", "and": "%s & %s",
       "sll": "%s << (%s & 31)", "srl": "%s >> (%s & 31)", "sra": "(u32)((s32)%s >> (%s & 31))",
       "slt": "(u32)((s32)%s < (s32)%s)", "sltu": "(u32)(%s < %s)", "mul": "%s * %s",
       "mulh": "(u32)(((s64)(s32)%s * (s64)(s32)%s) >> 32)",
       "mulhsu": "(u32)(((s64)(s32)%s * (s64)(u64)%s) >> 32)",
       "mulhu": "(u32)(((u64)%s * (u64)%s) >> 32)"}
DIVS = {"div": "({b} == 0 ? 0xffffffffu : ({a} == 0x80000000u && {b} == 0xffffffffu) ? 0x80000000u : "
               "(u32)((s32){a} / (s32){b}))",
        "divu": "({b} == 0 ? 0xffffffffu : {a} / {b})",
        "rem": "({b} == 0 ? {a} : ({a} == 0x80000000u && {b} == 0xffffffffu) ? 0u : (u32)((s32){a} % (s32){b}))",
        "remu": "({b} == 0 ? {a} : {a} % {b})"}
CSRS = ("csrrw", "csrrs", "csrrc", "csrrwi", "csrrsi", "csrrci")
TYPES = ("u8", "s8", "u16", "s16", "u32", "s32", "u64", "s64")     # dread.h's: no variable may take one


def cname(name):
    """The C variable of a register: its ABI name, s8 as s8_ (dread.h's type)."""
    return name + "_" if name in TYPES else name


class RiscVLifter(lift.Lifter):
    REGS, ARGS, AFTER_CALL = REGS, ARGS, AFTER_CALL

    # ---- the stack pointer's depth and the frame (dread.lift's states) ----

    def ret_jump(self, insn, values):
        """jalr zero, 0(rN) with the return address in rN (ra itself: flow calls that a return)."""
        f = fields(insn)
        return (f.op == "jalr" and f.rd == 0 and f.imm == 0 and f.rs1 not in (0, 1)
                and getattr(insn, "rv_target", None) is None and values.get(NAMES[f.rs1]) == self.RET)

    def frame_value(self, number, depth, values):
        """("sp", k) when register `number` holds an address in the frame (sp itself included)."""
        name = NAMES[number]
        if name == "sp":
            return ("sp", depth)
        v = values.get(name)
        return v if isinstance(v, tuple) and v and v[0] == "sp" else None

    @staticmethod
    def copied(f):
        """The source register of a plain copy (mv: addi rd, rs, 0 or add rd, zero, rs), or None."""
        if f.op == "addi" and f.imm == 0 and f.rs1:
            return f.rs1
        if f.op == "add" and (f.rs1 == 0) != (f.rs2 == 0):
            return f.rs2 or f.rs1
        return None

    def step(self, insn, depth, values):
        f = fields(insn)
        after = dict(values)
        reads, writes = self.arch.reg_effects(insn)
        new = self.new_depth(insn, depth, values) if "sp" in writes else depth
        known = self.made_value(insn, depth, values)
        slot = self.stored_slot(insn, depth, values)
        for r in writes:
            if r != "sp":
                after.pop(r, None)
        if self.arch.flow(insn)[0] in ("call", "icall"):
            for r in self.after_call(insn):
                after.pop(r, None)
        if known is not None:
            self._put(after, known[0], known[1])
        if slot is not None:
            self._put(after, slot[0], values.get(slot[1]) if slot[1] else None)
        return new, after

    def made_value(self, insn, depth, values):
        """(register, value) for addi rd, sp or a frame address, imm (a frame address), a plain
        copy, and lw rd from a frame slot (what the slot holds: a saved return address)."""
        f = fields(insn)
        if not f.rd or f.rd == 2:
            return None
        if f.op == "addi":
            v = self.frame_value(f.rs1, depth, values)
            if v is not None:
                return NAMES[f.rd], ("sp", v[1] - f.imm)
        src = self.copied(f)
        if src is not None:
            v = self.frame_value(src, depth, values) or values.get(NAMES[src])
            return (NAMES[f.rd], v) if v is not None else None
        if f.op == "lw":
            v = self.frame_value(f.rs1, depth, values)
            if v is not None:
                return NAMES[f.rd], values.get(v[1] - f.imm)
        return None

    def stored_slot(self, insn, depth, values):
        """(slot, register name or None for zero) of a word store into the frame."""
        f = fields(insn)
        if f.op != "sw":
            return None
        v = self.frame_value(f.rs1, depth, values)
        if v is None:
            return None
        return v[1] - f.imm, NAMES[f.rs2] if f.rs2 else None

    def ldm_slots(self, insn, depth, values):
        return None

    def new_depth(self, insn, depth, values):
        """The depth after addi sp, sp or a frame address, imm and mv sp, rN (a frame pointer)."""
        f = fields(insn)
        if f.op == "addi" and f.rd == 2:
            v = self.frame_value(f.rs1, depth, values)
            if v is not None:
                return v[1] - f.imm
        src = self.copied(f) if f.rd == 2 else None
        if src is not None:
            v = self.frame_value(src, depth, values)
            if v is not None:
                return v[1]
        if f.op == "lw" and f.rd == 2 and f.rs1 != 2:
            raise lift.StackSwitch("0x%x loads the stack pointer from memory not on the stack (a context switch?): "
                                   "%s %s" % (insn.address, insn.mnemonic, insn.op_str))
        raise Refused("0x%x changes the stack pointer in a way not modelled: %s %s" % (
            insn.address, insn.mnemonic, insn.op_str))

    def saving_store(self, insn, values):
        """sw rN into the frame: a save when rN still holds what the caller left."""
        f = fields(insn)
        if f.op == "sw" and f.rs2 and values is not None:
            base = NAMES[f.rs1]
            v = values.get(base) if base != "sp" else ("sp", 0)
            if isinstance(v, tuple) and v and v[0] == "sp":
                return NAMES[f.rs2]
        return None

    def plain_move(self, insn):
        f = fields(insn)
        src = self.copied(f)
        if src is None or not f.rd or 2 in (f.rd, src):
            return None
        return NAMES[f.rd], NAMES[src]

    def stack_args(self, fn):
        """Words above the entry's stack pointer the function reads (its stack arguments): RISC-V
        passes eight arguments in registers, and the translation refuses the rest (0 or None). [abi]
        stack_args overrides it for a function whose stack pointer is not static."""
        if fn.name in self._stack_args:
            return self._stack_args[fn.name]
        declared = self.prog.target.abi.stack_args.get(fn.name)
        if declared is not None:
            self._stack_args[fn.name] = declared
            return declared
        self._stack_args[fn.name] = 0
        try:
            states = self.states(fn, strict=False, switch=True)    # (a context switch: until it switches)
        except Refused:
            self._stack_args[fn.name] = None
            return None
        top = 0
        for a, (depth, values) in states.items():
            f = fields(self.prog.insn(a))
            if f.width is None:
                continue
            v = self.frame_value(f.rs1, depth, values)
            if v is not None and f.imm - v[1] >= 0:
                top = max(top, (f.imm - v[1] + f.width + 3) // 4)
        self._stack_args[fn.name] = top
        return top

    # ---- what kind of function ----

    def lift(self, fn):
        n = self.stack_args(fn)
        if n:
            raise Refused("it takes %d stack words (arguments beyond the eighth)" % n)
        return super().lift(fn)

    def compiled_isa(self):
        return "rv32"

    def exception_return(self, insn):
        return fields(insn).op in ("mret", "sret", "uret")

    def handler_kind(self, fn):
        """"machine" when the function's returns are mret (a machine-mode trap handler), else None."""
        if fn.name in self._handlers:
            return self._handlers[fn.name]
        returns = [i for i in self.prog.instructions(fn) if i is not None and self.arch.flow(i)[0] == "return"]
        traps = [i for i in returns if self.exception_return(i)]
        if traps and len(traps) != len(returns):
            raise Refused("it returns both from a trap and to a caller")
        if any(fields(i).op != "mret" for i in traps):
            raise Refused("it returns from a trap below machine mode")
        kind = "machine" if traps else None
        self._handlers[fn.name] = kind
        return kind

    def _check_returns(self, fn, states, handler=None):
        """Refuse a return to anything but the return address."""
        for a, (depth, values) in states.items():
            insn = self.prog.insn(a)
            if self.arch.flow(insn)[0] != "return" and not self.ret_jump(insn, values):
                continue
            if self.exception_return(insn):
                if handler:
                    continue
                raise Refused("0x%x returns from a trap: %s" % (a, insn.mnemonic))
            f = fields(insn)
            if values.get(NAMES[f.rs1]) != self.RET:
                raise Refused("0x%x returns to an address it computed, not to its caller" % a)

    def _wide(self, name):
        flow = self.flow
        return bool(flow and "a1" in flow.kept_regs(name) and "a1" not in flow.preserved_regs(name))

    def _check_kept(self, fn):
        """Refuse when callers read a register other than a0 and a1 that the function sets."""
        flow = self.flow
        if flow is None:
            return
        result = {"a0", "a1"} if self._wide(fn.name) else {"a0"}
        written = sorted(set(flow.kept_regs(fn.name)) - result - set(flow.preserved_regs(fn.name)))
        if written:
            raise Refused("its callers read %s, which it sets (%s); C returns a0 and a1 only" % (
                written[0], flow.why(fn.name, written[0])))

    def _check_after_call(self, callee, insn):
        """As the ARM translator's: a register the callee never writes keeps the caller's value."""
        flow = self.flow
        if flow is None:
            return
        result = {"a0", "a1"} if self._wide(callee.name) else {"a0"}
        preserved = set(flow.preserved_regs(callee.name))
        undef = self.ctx["undef"].get(insn.address, frozenset())
        for reg in flow.kept_regs(callee.name):
            if reg in preserved and reg not in undef:
                continue
            if reg not in result and (self.ctx["fn"].name, insn.address) in flow.sites_of(callee.name, reg):
                raise Refused("0x%x reads %s behind the call of %s (what the callee leaves)" % (
                    insn.address, reg, callee.name))

    # ---- the translation ----

    def reg(self, number, sp=False):
        """The C expression of a register read; sp only where `sp` allows it (an address)."""
        name = NAMES[number]
        if name == "zero":
            return "0u"
        if name == "sp":
            if not sp:
                raise Refused("0x%x computes with the stack pointer: %s %s" % (
                    self.insn_now.address, self.insn_now.mnemonic, self.insn_now.op_str))
            return "(sp0 - %d)" % self.ctx["depth"][self.insn_now.address]
        return cname(name)

    def address(self, f):
        """The address a load or store accesses: an access above the entry's stack pointer is refused."""
        if f.rs1 == 2:
            off = f.imm - self.ctx["depth"][self.insn_now.address]
            if off >= 0:
                raise Refused("0x%x reads or writes above its entry's stack pointer (stack arguments)"
                              % self.insn_now.address)
            return "(sp0 - %d)" % -off
        return "%s + 0x%xu" % (self.reg(f.rs1), f.imm & 0xFFFFFFFF) if f.imm else self.reg(f.rs1)

    def assign(self, f, expr):
        if not f.rd:
            return None                     # a write to zero: nothing (a hint, a nop)
        if f.rd == 2:
            return None                     # sp: its depth is known statically (states)
        return "%s = %s;" % (cname(NAMES[f.rd]), expr)

    def insn(self, insn):
        self.insn_now = insn
        f = fields(insn)
        op, a = f.op, insn.address
        kind, target, falls = self.arch.flow(insn)
        depth = self.ctx["depth"]
        if kind == "return" or self.ret_jump(insn, self.values()):
            if self.exception_return(insn):
                if self.ctx.get("handler"):
                    return "return;"        # clang's epilogue restores the registers and returns with mret
                raise Refused("0x%x returns from a trap: %s" % (a, insn.mnemonic))
            return "return %s;" % self._ret()
        if op in ("beq", "bne", "blt", "bge", "bltu", "bgeu"):
            if target not in depth:
                raise Refused("0x%x branches out of the function (0x%x)" % (a, target))
            return "if (%s) goto L_%x;" % (CONDS[op] % (self.reg(f.rs1), self.reg(f.rs2)), target)
        if kind == "jump":                  # jal zero, or auipc t1 + jalr zero to a known address
            if target in depth:
                return "goto L_%x;" % target
            callee = self.prog.by_entry.get(target)
            if callee is None:
                raise Refused("0x%x jumps into other code (0x%x)" % (a, target))
            return "%s return %s;" % (self._call(callee, insn), self._ret())
        if kind == "call":
            if f.rd != 1:
                raise Refused("0x%x calls with the return address in %s" % (a, NAMES[f.rd]))
            callee = self.prog.by_entry.get(target)
            if callee is None:
                raise Refused("0x%x calls into the middle of something (0x%x)" % (a, target))
            text = self._call(callee, insn)
            return text + " for (;;) { }" if self.never_returns(callee.name) else text
        if kind == "icall":
            if op == "ecall":
                return self._ecall()
            if op != "jalr" or f.rd != 1:
                raise Refused("0x%x: %s is not modelled" % (a, insn.mnemonic))
            fp = self.reg(f.rs1) + (" + 0x%xu" % (f.imm & 0xFFFFFFFF) if f.imm else "")
            return self._icall(insn, "(%s)" % fp)
        if kind == "ijump":
            return self._switch_jump(insn)
        if kind == "stop":
            raise Refused("0x%x: %s" % (a, insn.mnemonic))
        if op == "lui":
            return self.assign(f, "0x%xu" % (f.imm & 0xFFFFFFFF))
        if op == "auipc":                   # where the original's code is: a constant
            return self.assign(f, "0x%xu" % ((a + f.imm) & 0xFFFFFFFF))
        if op in LOADS:
            if f.rd == 2:
                raise Refused("0x%x loads the stack pointer" % a)
            return self.assign(f, LOADS[op] % self.address(f))
        if op in STORES:
            ctype = STORES[op]
            value = self.reg(f.rs2)
            return "*(volatile %s *)(%s) = %s;" % (ctype, self.address(f), value if ctype == "u32" else
                                                   "(%s)%s" % (ctype, value))
        if op == "addi":
            if f.rd == 2:
                return None                 # a frame adjustment (states follows it)
            src = self.reg(f.rs1, sp=True)
            return self.assign(f, src if not f.imm else ALU_IMM["addi"] % (src, f.imm & 0xFFFFFFFF))
        if op in ALU_IMM:
            return self.assign(f, ALU_IMM[op] % (self.reg(f.rs1), f.imm & 0xFFFFFFFF))
        if op == "slti":
            return self.assign(f, "(u32)((s32)%s < %d)" % (self.reg(f.rs1), f.imm))
        if op == "sltiu":
            return self.assign(f, "(u32)(%s < 0x%xu)" % (self.reg(f.rs1), f.imm & 0xFFFFFFFF))
        if op == "slli":
            return self.assign(f, "%s << %d" % (self.reg(f.rs1), f.imm))
        if op == "srli":
            return self.assign(f, "%s >> %d" % (self.reg(f.rs1), f.imm))
        if op == "srai":
            return self.assign(f, "(u32)((s32)%s >> %d)" % (self.reg(f.rs1), f.imm))
        if op in ALU:
            if f.rd == 2:
                raise Refused("0x%x computes the stack pointer: %s %s" % (a, insn.mnemonic, insn.op_str))
            src = self.copied(f)
            if src is not None:             # mv rd, rs (sp allowed: a frame address)
                return self.assign(f, self.reg(src, sp=True))
            return self.assign(f, ALU[op] % (self.reg(f.rs1), self.reg(f.rs2)))
        if op in DIVS:
            return self.assign(f, DIVS[op].format(a=self.reg(f.rs1), b=self.reg(f.rs2)))
        if op in CSRS:
            return self._csr(f, insn)
        if op == "fence":
            return '__asm__ volatile ("fence" ::: "memory");'
        if op == "fence.i":
            return '__asm__ volatile ("fence.i" ::: "memory");'
        if op == "wfi":
            return '__asm__ volatile ("wfi" ::: "memory");'
        raise Refused("0x%x: %s %s is not modelled" % (a, insn.mnemonic, insn.op_str))

    def _csr(self, f, insn):
        """A CSR instruction as itself, its registers bound to the C variables."""
        imm = f.op.endswith("i")
        source = "%d" % f.imm if imm else ("zero" if not f.rs1 else "%1")
        inputs = "" if imm or not f.rs1 else ' "r"(%s)' % self.reg(f.rs1)
        if f.rd and f.rd != 2:
            return '__asm__ volatile ("%s %%0, 0x%x, %s" : "=r"(%s) :%s : "memory");' % (
                f.op, f.csr, source, cname(NAMES[f.rd]), inputs)
        source = source.replace("%1", "%0")
        return '__asm__ volatile ("%s zero, 0x%x, %s" : :%s : "memory");' % (f.op, f.csr, source, inputs)

    def _ret(self):
        if self.ctx.get("handler"):
            return ""
        return "((u64)a1 << 32 | a0)" if self._wide(self.ctx["fn"].name) else "a0"

    def _args(self, insn, callee=None):
        nstack = self.stack_args(callee) if callee is not None else 0
        if nstack is None:
            raise Refused("0x%x calls %s, whose stack arguments are not known" % (insn.address, callee.name))
        if nstack:
            raise Refused("0x%x calls %s, which takes stack arguments" % (insn.address, callee.name))
        return list(ARGS)

    def _call(self, callee, insn):
        if not IDENT.match(callee.name):
            raise Refused("0x%x calls %s, whose name is not a C identifier" % (insn.address, callee.name))
        args = self._args(insn, callee)
        leaves = self.leaves(callee)
        if leaves:
            raise Refused("0x%x calls %s, which reads %s its caller leaves" % (
                insn.address, callee.name, ", ".join(leaves)))
        self._check_after_call(callee, insn)
        wide = self._wide(callee.name)
        self.ctx["callees"].add((callee.name, 0, wide))
        call = "%s(%s)" % (callee.name, ", ".join(args))
        keep = set(self.flow.preserved_regs(callee.name)) if self.flow else set()
        cleared = [r for r in SCRATCH if r not in (("a0", "a1") if wide else ("a0",)) and r not in keep]
        clear = " = ".join(cleared) + " = 0;" if cleared else ""
        if wide:
            return "{ u64 r = %s; a0 = (u32)r; a1 = (u32)(r >> 32); } %s" % (call, clear)
        return "a0 = %s; %s" % (call, clear)

    def _icall(self, insn, fp):
        clear = " = ".join(r for r in SCRATCH if r != "a0") + " = 0;"
        return "a0 = ((u32 (*)(%s))%s)(%s); %s" % (", ".join(["u32"] * len(ARGS)), fp, ", ".join(ARGS), clear)

    def _switches(self, fn):
        super()._switches(fn)
        for a, (check, reg, values) in list(self.ctx["switch"].items()):
            self.ctx["switch"][a] = (check, cname(reg), values)

    def _switch_jump(self, insn):
        got = self.ctx["switch"].get(insn.address)
        if got is None:
            raise Refused("0x%x jumps through a register to targets not known" % insn.address)
        check, reg, values = got
        cases = " ".join("case %d: goto L_%x;" % (k, t) for k, t in sorted(values.items()))
        return "switch (index) { %s default: return %s; }" % (cases, self._ret() or "")

    def after_call(self, insn):
        """An ecall leaves ra alone (a trap returns by mepc); it may change the other scratch registers."""
        if fields(insn).op == "ecall":
            return self.AFTER_CALL - {"ra"}
        return self.AFTER_CALL

    def _ecall(self):
        """ecall with a0-a7 bound to the handler (its arguments and results); the other scratch
        registers and ra are taken as changed, as a call may change them."""
        binds = ", ".join('x%d __asm__("a%d") = a%d' % (i, i, i) for i in range(8))
        outs = ", ".join('"+r"(x%d)' % i for i in range(8))
        back = " ".join("a%d = x%d;" % (i, i) for i in range(8))
        return ('{ register u32 %s; __asm__ volatile ("ecall" : %s :: "t0", "t1", "t2", "t3", "t4", "t5", "t6", '
                '"ra", "memory"); %s }' % (binds, outs, back))

    def render(self, fn, body):
        ctx = self.ctx
        lines = ["/* %s at 0x%x, lifted by dread lift: one statement per instruction. */" % (fn.name, fn.entry),
                 '#include "dread.h"']
        for name, nstack, wide in sorted(ctx["callees"]):
            lines.append("extern %s %s(%s);" % ("u64" if wide else "u32", name, ", ".join(["u32"] * len(ARGS))))
        regs = [r for r in REGS if r not in ("sp", "pc")]
        if ctx.get("handler"):              # clang writes the entry and mret
            lines.append('void __attribute__((interrupt("machine"))) %s(void)' % fn.name)
            lines.append("{")
            init = ["%s = 0" % cname(r) for r in regs]
        else:
            params = ["u32 p%d" % i for i in range(len(ARGS))]
            lines.append("%s %s(%s)" % ("u64" if self._wide(fn.name) else "u32", fn.name, ", ".join(params)))
            lines.append("{")
            init = ["ra = (u32)__builtin_return_address(0)" if r == "ra" else
                    "%s = p%d" % (r, ARGS.index(r)) if r in ARGS else "%s = 0" % cname(r) for r in regs]
        lines.append("    u32 %s;" % ", ".join(init))
        lines += self.fixed_lines(cname)
        frame = (ctx["frame"] + 15) & ~15
        lines.append("    u32 frame[%d] __attribute__((aligned(16)));" % max(4, frame // 4))
        lines.append("    u32 sp0 = (u32)&frame[%d];" % (frame // 4))
        if ctx["switch"]:
            lines.append("    u32 index = 0;")
        lines.append("    %s (void)sp0;" % " ".join("(void)%s;" % cname(r) for r in regs))
        lines += body
        lines.append("}")
        return "\n".join(lines) + "\n"
