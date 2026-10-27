"""dread regflow: the registers a function's callers read after it returns, and those it reads at
its entry.

The calling convention says which registers survive a call (callee-saved) and which do not
(scratch: arguments, result and the rest). A compiler that sees the callee may rely on more: GCC's
-fipa-ra (on at -O2 and -Os) keeps a value in a scratch register across a call when the callee,
compiled earlier in the same file, does not touch that register. Hand-written assembly does the
same with the condition flags or a second result register. The caller then depends on what the
callee actually leaves in those registers, and a callee rewritten in C, compiled alone and by the
convention, may break it while its own tests pass: they compare the result register, memory and
the peripheral log, not the scratch registers. gizmo-a32 has one: uart_puts keeps its string
pointer in r1 across bl uart_putc.

For every function F this finds:

  kept         the scratch registers and flags some caller reads after a call to F before writing
               them. F's value of them at the return is behaviour (a result, or a register F must
               leave as it found it). dread test and dread integration compare them at the return.
               The result registers (the first, with hard float the FPU's) as the program reads them:
               where some caller's behaviour depends on them, or a pointer may call F (pointer_entries);
               every other register as the comparison needs it: where the outputs some caller's cases
               compare depend on it, its result at every return among them (Analysis.solve).
  reads        the scratch registers and flags F reads at its entry before writing them: its
               register arguments, and anything else its callers have to set up.
  leftover     the functions whose second result register their callers read (kept, and F writes
               it: the lifted C returns two words) where it holds only a constant F put there or what
               a callee left there that is itself such a value: no result of the source's
               (Analysis.leftovers; dread readable hands it back through dread.h's RETURN2).
  overwritten  the scratch registers F writes on every path from its entry to a return without
               reading first what its caller left there: what F hands back in them is its own,
               whatever its caller passed (dread readable passes no argument in a result register of
               such a callee: tidy.trim_calls). The registers written on every path (_must_write,
               its callees' and tail callees' included) less those whose value from the caller F may
               read (_reads_first): reads alone does not do, since a call ends there whatever its
               callee may write, and a callee that hands its argument back unwritten on some path
               (int g(int x) { if (c) return 7; return x; }) lets the caller's value through to what
               F computes from g's result.

Scratch registers are the general registers the target's [abi] does not list as callee-saved (ARM
with the default ABI: r0-r3 and r12), and the flags.

Method: register liveness over each function's instructions, solved for all functions together. A
return passes on kept[F]; a call to G uses reads[G] and ends the registers G may write on any path
(its callees included), so a register G never writes flows through the call; a branch to another
function's entry is a tail call (G returns to F's caller, so kept[G] includes kept[F]).
Conditional instructions read the flags and end nothing. Since regflow 26 bit for bit, and a read
counts only where its bits matter: an instruction that only computes registers reads what its used
results need (Analysis.refinement: bic r1, r1, #0xff whose result's low byte alone is stored reads none
of r1; one whose result nobody uses reads nothing; the layers' bit_demand rules, checked against the
emulator), a store of a byte the low byte. Before, every read counted, and a caller that kept the upper
bits of a register a callee left and stored none of them made that register a second result. What a
function computes for a callee the analysis does not see (a call through a pointer, a variadic
function's unnamed arguments, a way out it cannot follow) stays read (Analysis._handed).

It errs on the side of finding less, so that what it finds can be compared without false alarms:
pushes do not count as reads (push {r3, lr} that only keeps the stack aligned would make r3 an
argument), except where a call takes the word a push stores as a stack argument and the layer says
pushes pass arguments (x86's cdecl: push edx; ...; call memcpy; GCC's pushes of a dead register that
pad the stack in front of the arguments or only reserve a word stay no reads); an indirect call (and an svc, whose handler may return values anywhere) reads nothing
and ends every scratch register; an indirect jump goes on
at the function's code that no direct path reaches (the cases of a switch), and where there is none
it is taken for an indirect tail call that passes nothing on. Not seen: what the callee of an
indirect call reads, paths through code the function table does not cover, and callee-saved
registers (a function that reads one its caller left: `caller_regs` in the case files).

Output: each function whose kept registers go beyond the first result register, with the call sites
that read them; with --all every function's reads, kept and overwritten. --json FILE writes every
function's facts besides, or the named functions' (docs/formats.md, "dread regflow --json"): reads,
kept, preserved, overwritten, leftover, computed, the bits a read uses where it uses fewer than all,
the call sites after which its callers read its registers, and its own calls after which it reads a
callee's; dread gui's registers view shows the same facts (RegFlow.facts). Cached in BUILD/regflow/IMAGE.json,
keyed by the image, its tables, its startup copies, the ABI and the source of the analysis (this module,
dread.switches, dread.program's decoder, the arch layer and its package's helpers) as the process read it
when it imported this module, not when it stores (a run that imported older code keys by that code).

    dread regflow [--target DIR] [--image NAME] [--all] [--why] [--json FILE] [NAME...]
"""
import argparse
import collections
import hashlib
import json
import os
import struct
import sys

from dread.arch import code_address
from dread import target as tgt

VERSION = 34                # 3: entry spills (variadic functions) are no use; 4: only a variadic save area
                            # is (-O0 keeps its arguments so and uses them), and a save around a call of a
                            # register never set before is neither a read nor a write
                            # 5: MSP430's sub and subc of a register with itself read nothing
                            # 6: a call through a register that holds one function's entry (the
                            # layer's register_call_targets: MSP430) is a call of that function, and
                            # a runtime helper's result by the ABI (abi_results) counts as read;
                            # 7: and by the target's compiler profile (dread cclab)
                            # 8: umlal and smlal (and the DSP extension's accumulating pairs) read
                            # their RdLo and RdHi, ssat16 and usat16 their source: capstone's lists
                            # left those out (dread.arch.arm.operand_roles)
                            # 9: the FPU's block transfers (vldm writes, vstm reads, vpush and vpop),
                            # vmrs APSR_nzcv writes the flags, d<n> is s<2n> and s<2n+1>
                            # 10: with hard float, s0-s15 are followed like the scratch registers
                            # 11: vpush and vpop, and libgcc's eight-byte push and pop of one register
                            # (str lr, [sp, #-8]!; ldr pc, [sp], #8: a return), are pushes and pops;
                            # a bl to a helper that calls another is a call, not a switch's dispatch
                            # 12: the MSP430's flags one by one (the layer's flag_effects): clrc writes C
                            # and passes Z, N and V through instead of reading the flags as one
                            # 13: a register such a save and restore enclose a call with is not read
                            # behind that call (the restore replaces the callee's value unread)
                            # 14: sp stays a base of frame stores when an instruction moves it (sub sp):
                            # clang's variadic save areas (sub sp, #8; strd r2, r3, [sp, #16]) went unseen
                            # 15: a copy of a base plus a constant is a base too (the layer's base_copy):
                            # clang 18 keeps a frame pointer and reaches the save area through it (add r7,
                            # sp, #8; add.w r12, r7, #8; stm.w r12, {r1, r2, r3}), unseen until then
                            # 16: a conditional branch's taken edge carries the writes made under its
                            # condition, its fall-through edge those under the opposite one (it eq; ldreq
                            # r3, [r1]; beq L; ... L: cmp r2, r3 reads no r3 of the caller: Ubuntu's and
                            # Debian's newlib strcmp, which made its callers' callers look as if they read
                            # r3 on every older-toolchain build of the benchmark)
                            # 17: a flag write under a condition keeps what was written under that condition
                            # (it ran when the condition holds afterwards: libgcc's __aeabi_fmul seemed to
                            # read r3, and every soft-float caller that kept a value in r3 was refused)
                            # 18: the ARM layer's abi_results (the RTABI's helpers: __aeabi_uldivmod
                            # returns r0-r3, __aeabi_uidivmod r0 and r1, ...)
                            # 19: such a save and restore is a copy through the slot: the save reads the
                            # register when it is live behind the restore, which writes it (newlib's
                            # _free_r spills r0 around __malloc_lock, which writes r0, and stores errno
                            # through it after: r0 was not seen read; found by dread callconv)
                            # 20: what a call itself writes (its link register) is no read of the callee's
                            # in front of the call: picolibc's _init_tls enters GCC's millicode with jal t0,
                            # __riscv_save_0, which returns through t0, and every FreeRTOS function on a
                            # path to it looked as if it read t0 (GCC 12 to 14 with picolibc: 19 functions
                            # refused for reading t0 behind vAssertCalled alone)
                            # 21: an argument register an entry push of the argument registers saved (a
                            # variadic function's save area) is read where a load at a known offset reads
                            # its slot back: GCC's printf pushes r0-r3 and loads its format from r0's slot,
                            # sprintf keeps its format r1 there (found by dread callconv on every ARM build)
                            # 22: a save area lies right below the stack arguments (the last argument
                            # register just below the entry's stack pointer): FreeRTOS's xStreamBufferSend
                            # spills its fourth argument r3 at the entry far below, and was taken for
                            # variadic, its r3 no use (dread callconv, GCC -O2)
                            # 23: an instruction that only computes callee-saved registers nothing reads
                            # afterwards reads nothing: GCC 10 on RISC-V copies a register nothing set into
                            # s0 and never reads s0 again (xTaskCheckForTimeOut: mv s0, a4), and a4 seemed an
                            # input of it and of every function on a path to it (13 refused on that build)
                            # 24: where a store or load goes in the frame says the lifter's states (Frames:
                            # sp, a frame pointer, any copy, a frame address loaded back from a slot) instead
                            # of shapes of regflow's own (the layers' sp_copy, base_copy, frame_stores and
                            # frame_slot, sp-relative only); saves and restores pair by depth
                            # 25: overwritten, the registers a function writes on every path to its return
                            # without reading what its caller left there first (dread readable passes no
                            # phantom argument in a result register); kept, reads and preserved as before
                            # 26: a read of bits nobody uses is no read (Analysis.refinement: an instruction
                            # that only computes registers reads what its used results need, bit for bit
                            # where the layer has a rule; a store of a byte the low byte); a function a
                            # pointer may call has its result read (pointer_entries); leftover and computed
                            # 27: the bits a read uses kept beside the registers (bits: kept's and reads'
                            # fields where fewer than all of a register's bits are used); the analysis is
                            # the one of 26
                            # 28: a call of a function that takes a printf format whose format is a constant
                            # uses the registers the format takes (dread.varargs call_arities: FreeRTOS's
                            # vAssertCalled hands its line on to printf and reads it); variadic and formats
                            # 29: a system call reads what the target says its handler reads ([abi]
                            # syscall_reads), where it says; where not, nothing, as before
                            # 30: a write of part of a register from memory reads the kept bits only where
                            # they are used (the layer's merge_demand), and a flag setter the operand bits
                            # its flags depend on where the layer says (flag_demand): x86's mov dl, byte ptr
                            # [eax]; cmp dl, 0x20 read edx at every function's entry (gizmo-i386's parse_kv)
                            # 31: a push of a register into a word a call takes as a stack argument reads
                            # it, where the layer says pushes pass arguments (pushes_arguments: x86's
                            # cdecl; Analysis.argument_pushes): FreeRTOS's prvCopyDataFromQueue pushes its
                            # edx as memcpy's first argument, and read eax alone
                            # 32: an x86 string instruction reads no flags (the direction flag alone, which
                            # the ABI keeps clear): rep movsl made FreeRTOS's inl hand its flags back
                            # 33: x86's padding no-ops read nothing (the layer's is_padding: lea esi, [esi]
                            # made every function GCC -O2 aligned a loop in take its caller's esi)
                            # 34: x86's frames aligned at run time (and esp, -8) have the lifter's states
                            # (lift_x86 realigns), where every answer was the cautious one before


class RegFlow:
    """The result: kept, reads and preserved per function name, with the call sites behind kept
    (sites: name -> register -> [[caller, address, always]], always: read on every path after the
    call; [POINTER_CALLERS, 0, False] for the result of a function a pointer may call). preserved: the
    kept registers the function never writes, its callees included: its callers rely on it leaving them
    alone. overwritten: the registers whose value at a return is the function's own, never what its
    caller left there (the module's docstring); None where the result came from before version 25
    (nothing is known overwritten then). leftover: the functions whose second result register their
    callers read but holds only a constant or what a callee left there (Analysis.leftovers); computed:
    the functions whose first result register may hold at a return a value their own instructions
    wrote (Analysis.computed); both None before version 26. bits: {"kept": {name: {register: mask}},
    "reads": {...}}, the bits of each register a read uses where they are fewer than all of it (bic r1,
    r1, #0xff whose result's low byte alone is stored: none of r1's low byte; a byte's store: 0xff), a
    register absent where every bit counts; None before version 27."""

    def __init__(self, names, kept, reads, sites, preserved=None, overwritten=None, leftover=None, computed=None,
                 bits=None, variadic=None, formats=None):
        self.names = names                  # register names in bit order
        self.kept, self.reads, self.sites = kept, reads, sites
        self.preserved = preserved or {}
        self.overwritten = overwritten
        self.leftover = leftover
        self.computed = computed
        self.bits = bits
        self.variadic = variadic
        self.formats = formats
        self._by_caller = None              # caller -> {(address, callee): {register: always}} (calls_of)

    def _regs(self, value):
        return [n for i, n in enumerate(self.names) if value >> i & 1]

    def kept_regs(self, name):
        return self._regs(self.kept.get(name, 0))

    def read_regs(self, name):
        return self._regs(self.reads.get(name, 0))

    def preserved_regs(self, name):
        return self._regs(self.preserved.get(name, 0))

    def overwritten_regs(self, name):
        """The registers whose value at name's return is its own, never its caller's: written on every
        path to a return, what its caller left there never read first ([] where that is not known)."""
        return self._regs(self.overwritten.get(name, 0)) if self.overwritten else []

    def sites_of(self, name, reg):
        """[(caller, address)] of the calls after which `reg` of `name` is read; a tail call is
        listed with the address of its branch."""
        return [tuple(s[:2]) for s in self.sites.get(name, {}).get(reg, [])]

    def always(self, name, reg):
        """Whether some caller reads `reg` on every path after its call (else only on some)."""
        return any(len(s) > 2 and s[2] for s in self.sites.get(name, {}).get(reg, []))

    def why(self, name, reg, limit=3):
        """A short text for reports: who reads the register after the call, and how."""
        sites = self.sites_of(name, reg)
        text = "read after the call by " + ", ".join("%s 0x%x" % s if s[1] else "%s (its result)" % s[0]
                                                      for s in sites[:limit])
        if len(sites) > limit:
            text += " and %d more" % (len(sites) - limit)
        if not self.always(name, reg):
            text += ", on some paths only"
        if reg in self.preserved_regs(name):
            text += "; the original leaves it unchanged"
        return text

    def is_leftover(self, name):
        """Whether the function's second result register, which its callers read, holds only a constant
        or what a callee left there (leftover; False where that is not known)."""
        return bool(self.leftover) and name in self.leftover

    def to_json(self):
        out = {"names": self.names, "kept": self.kept, "reads": self.reads, "sites": self.sites,
               "preserved": self.preserved}
        if self.overwritten is not None:
            out["overwritten"] = self.overwritten
        if self.leftover is not None:
            out["leftover"] = self.leftover
        if self.computed is not None:
            out["computed"] = self.computed
        if self.bits is not None:
            out["bits"] = self.bits
        if self.variadic is not None:
            out["variadic"] = self.variadic
        if self.formats is not None:
            out["formats"] = self.formats
        return out

    @classmethod
    def from_json(cls, data):
        return cls(data["names"], data["kept"], data["reads"], data["sites"], data.get("preserved"),
                   data.get("overwritten"), data.get("leftover"), data.get("computed"), data.get("bits"),
                   data.get("variadic"), data.get("formats"))

    def case_facts(self):
        """What a function's generated cases depend on of the result (dread.autocase's environment):
        to_json without overwritten, leftover, computed, bits, variadic and formats, which no case reads,
        so that version 25 kept every image's cases (version 26 changed kept, reads and sites; 27 added
        bits alone; 28 changed reads, kept and sites where a format is known, and added the last two)."""
        out = self.to_json()
        for field in ("overwritten", "leftover", "computed", "bits", "variadic", "formats"):
            out.pop(field, None)
        return out

    def _order(self, reg):
        return (self.names.index(reg) if reg in self.names else len(self.names), reg)

    def calls_of(self, name):
        """The calls a function makes after which it reads a callee's register, from sites: [{"address",
        "callee", "registers": [{"register", "always"}]}] by address (a tail call that passes a register
        on counts as reading it)."""
        if self._by_caller is None:
            index = {}
            for callee, by_reg in self.sites.items():
                for reg, entries in by_reg.items():
                    for e in entries:
                        regs = index.setdefault(e[0], {}).setdefault((e[1], callee), {})
                        regs[reg] = bool(regs.get(reg) or (len(e) > 2 and e[2]))
            self._by_caller = index
        return [{"address": address, "callee": callee, "registers": self._listed(regs)}
                for (address, callee), regs in sorted(self._by_caller.get(name, {}).items())]

    def _listed(self, regs):
        """[{"register", "always"}] of {register: always}, in the registers' order."""
        return [{"register": r, "always": a} for r, a in sorted(regs.items(), key=lambda kv: self._order(kv[0]))]

    def facts(self, name):
        """What the result says of one function (dread regflow --json, dread gui's registers view):
        {"reads", "kept", "preserved", "overwritten" (null where not known: before version 25),
        "leftover", "computed" (whether it is one; null where not known), "bits" ({"reads", "kept"}: the
        bits used where fewer than all, as bits; null before version 27), "callers" (each call site after
        which its registers are read: [{"caller", "address", "registers": [{"register", "always"}]}]; a
        caller that stands for others, such as POINTER_CALLERS or "the ABI", with address 0), "calls"
        (calls_of), "variadic" (whether it is: its save area, or a printf format it takes; null before
        version 28) and "formats" (its calls of variadic functions: [{"address", "callee", "arguments",
        "format"}], how many argument registers each passes and the format's address, both null where the
        format is not known; null before version 28)}."""
        callers = {}
        for reg, entries in self.sites.get(name, {}).items():
            for e in entries:
                regs = callers.setdefault((e[0], e[1]), {})
                regs[reg] = bool(regs.get(reg) or (len(e) > 2 and e[2]))
        bits = None
        if self.bits is not None:
            bits = {k: dict(sorted(self.bits.get(k, {}).get(name, {}).items(), key=lambda kv: self._order(kv[0])))
                    for k in ("reads", "kept")}
        return {"reads": self.read_regs(name), "kept": self.kept_regs(name), "preserved": self.preserved_regs(name),
                "overwritten": self.overwritten_regs(name) if self.overwritten is not None else None,
                "leftover": name in self.leftover if self.leftover is not None else None,
                "computed": name in self.computed if self.computed is not None else None,
                "bits": bits,
                "callers": [{"caller": c, "address": a, "registers": self._listed(regs)}
                            for (c, a), regs in sorted(callers.items(), key=lambda kv: (kv[0][1] == 0, kv[0]))],
                "calls": self.calls_of(name),
                "variadic": name in self.variadic if self.variadic is not None else None,
                "formats": [{"address": a, "callee": c, "arguments": n, "format": f}
                            for a, c, n, f in self.formats.get(name, ())] if self.formats is not None else None}


def argued_path(image):
    """kept.tsv beside the image's cases directory."""
    return os.path.join(os.path.dirname(image.cases), "kept.tsv")


def load_argued(image):
    """{(function, register): argument} from the image's kept.tsv: kept registers that are not
    compared, each with the argument why the difference cannot matter."""
    out = {}
    path = argued_path(image)
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                fields = line.rstrip("\n").split("\t")
                if len(fields) >= 3 and not line.startswith("#"):
                    out[(fields[0].strip(), fields[1].strip())] = fields[2].strip()
    return out


Row = collections.namedtuple("Row", "address size kind target falls uses kills writes conditional stack written covered "
                                   "fall_covered demand", defaults=(0, None))


def _rows(program, fn, an, entry_name):
    """The function's instructions as Rows: control flow (arch.flow), and the scratch registers
    each one reads (uses) and certainly writes (kills), as masks (Analysis: a field of bits for each
    register). demand: what of uses counts, where that depends on more than the instruction itself
    (Analysis.refinement): an instruction that only computes registers reads what its used results
    need, a store of a byte or a halfword the bits it stores; None where every bit of uses counts.

    Predication: a register written under a condition counts as written for later reads under
    the same condition, and a write under the opposite condition completes it to a write that
    ends the register, as long as the flags do not change and no other path joins in between.
    (ldrls r3, =table; ldrls r0, [r3, r0, lsl #2] reads no r3 of the caller; ite eq; moveq r0, #1;
    movne r0, #0 ends r0.)

    A system call (the layer's is_syscall) reads what the target says its handler reads ([abi]
    syscall_reads), where it says; else nothing (an indirect call: _handed)."""
    arch = program.arch
    mask = an.mask
    syscall_reads = getattr(program.target.abi, "syscall_reads", None)
    is_syscall = getattr(arch, "is_syscall", lambda insn: False)
    insns = [program.insn(a) for a in fn.insns]
    joins = {fn.entry}
    for insn in insns:
        if insn is not None:
            kind, target, _ = arch.flow(insn)
            if kind == "jump" and target is not None:
                joins.add(program.code_address(target))
    out, state = [], {}
    flags = mask(["flags"])
    # The entry's straight run: a variadic function's save area, the unnamed argument registers
    # stored in its frame before anything writes them, is no use, like a push: clang's variadic
    # functions keep r1-r3 so (add r0, sp, #12; stm r0!, {r1, r2, r3}) where GCC pushes them, and
    # printf made every caller of an assertion handler look as if it read r3. Other stores so stay
    # uses: -O0 keeps every argument in its frame and loads it later.
    entry, written = True, set()
    passed = an.argument_pushes(fn)         # (pushes that pass a call's stack argument: they read)
    frames = Frames(program, fn) if hasattr(arch, "store_slots") else None
    save_area = variadic_save_area(program, fn, insns, joins, frames) if frames is not None else set()
    if save_area or pushed_save_area(program, insns):
        an.variadic.add(fn.name)
    for a, insn in zip(fn.insns, insns):
        if a != fn.entry and a in joins:
            entry = False
        if a in joins or (out and out[-1].kind not in ("next",) and not out[-1].falls):
            state = {}
        if insn is None:
            out.append(Row(a, program.insn_size(a), "stop", None, False, 0, 0, 0, False, None, set(), 0))
            state = {}
            continue
        kind, target, falls = arch.flow(insn)
        reads, writes = arch.reg_effects(insn)
        if syscall_reads is not None and is_syscall(insn):
            reads = set(reads) | set(syscall_reads)     # (what the target says its handler reads)
        if hasattr(arch, "flag_effects"):      # the flags one by one (scratch_names)
            fr, fw = arch.flag_effects(insn)
            reads = (set(reads) - {"flags"}) | {"flags:" + n for n in fr}
            writes = (set(writes) - {"flags"}) | {"flags:" + n for n in fw}
        stack = arch.stack_kind(insn)
        if entry and save_area:
            spilled = {r for r, _ in frames.stores(a, insn) if r not in written} & save_area
            if spilled:
                reads = set(reads) - spilled
            written |= set(writes)
            if kind != "next":
                entry = False
        rm, wm = (0 if stack == "push" and a not in passed else mask(reads)), mask(writes)
        pred = arch.predicate(insn)
        covered = fall_covered = 0
        if pred is None:
            uses, kills = rm, wm
        else:
            covered = state.get(pred, 0)        # also what a predicated call's callee reads
            uses = rm & ~covered
            kills = wm & state.get(arch.complement(pred), 0)
            if kind == "jump":                  # a conditional branch: what its fall-through edge knows written
                fall_covered = state.get(arch.complement(pred), 0)
            state[pred] = covered | wm
        demand = an.refinement(insn, reads, writes, pred is not None, rm) if rm else None
        out.append(Row(a, insn.size, kind, target, falls, uses, kills, wm, pred is not None, stack, writes, covered,
                       fall_covered, demand))
        if kind in ("call", "icall"):
            state = {}
        elif wm & flags:
            # New flags end what was known under every condition but the instruction's own: one
            # under a condition that holds now ran (skipped, it would have left the flags, and the
            # condition false), and so did everything before it under that condition (libgcc's
            # __aeabi_fmul: ittt ne; andsne r3, ...; teqne r2, ip; teqne r3, ip reads its own r3).
            state = {pred: state[pred]} if pred is not None else {}
    loaded = save_area_reads(program, fn, insns, frames)
    if loaded and out:                      # the push saved them for those loads: a read of each
        out[0] = out[0]._replace(uses=out[0].uses | mask(loaded))
    return out


_STATES = {}                                # id(program) -> a lifter without regflow, for its states()


def frame_states(program, fn):
    """The lifter's states of fn (dread.lift: before each instruction a path reaches, how far sp is
    below the entry's and what each register and frame slot holds; ("sp", k) is the address k bytes
    below the entry's stack pointer), up to a context switch; None when the lifter refuses the stack
    pointer's moves (start-up code that sets sp) or anything else fails."""
    from dread import lift
    lifter = _STATES.get(id(program))
    try:
        if lifter is None or lifter.prog is not program:
            lifter = _STATES[id(program)] = lift.make_lifter(program)
        return lifter.states(fn, strict=False, switch=True)
    except Exception:
        return None


class Frames:
    """One function's frame as the lifter's states see it (frame_states): which depth below the
    entry's stack pointer a register addresses, whatever it is (sp, a frame pointer, a copy of either
    plus or minus a constant, a frame address kept in a slot and loaded back). One analysis instead of
    a shape per compiler: regflow followed sp and its copies by shapes of its own (a copy of sp, then a
    copy of that copy for clang 18's frame pointer, regflow 15), each found when a compiler used one
    it lacked. ok is False without states; every answer is then the cautious one (no slot known, a
    frame address taken). The states are made when first asked for (callers check cheaply first
    whether they could matter: making them for every function took regflow from 79 to 138 seconds
    over the benchmark's builds)."""

    def __init__(self, program, fn):
        self.program, self.arch, self.fn = program, program.arch, fn
        self._states = False                # (not made yet)
        self._addressed = None

    @property
    def states(self):
        if self._states is False:
            self._states = frame_states(self.program, self.fn)
        return self._states

    @property
    def ok(self):
        return self.states is not None

    def depth(self, address, base):
        """How far below the entry's stack pointer the register base points before the instruction at
        address, or None."""
        state = self.states.get(address) if self.ok else None
        if state is None:
            return None
        depth, values = state
        value = ("sp", depth) if base == "sp" else values.get(base)
        if isinstance(value, tuple) and len(value) == 2 and value[0] == "sp":
            return value[1]
        return None

    def access(self, address, insn):
        """("store" or "load", register, depth) of a single register's store or load in the frame (the
        layer's slot_access), else None."""
        got = self.arch.slot_access(insn) if insn is not None and hasattr(self.arch, "slot_access") else None
        if got is None:
            return None
        kind, reg, base, disp = got
        k = self.depth(address, base)
        return (kind, reg, k - disp) if k is not None else None

    def stores(self, address, insn):
        """[(register, depth)] of each register a store puts in the frame (the layer's store_slots)."""
        if insn is None or not hasattr(self.arch, "store_slots"):
            return []
        out = []
        for reg, base, disp in self.arch.store_slots(insn):
            k = self.depth(address, base)
            if k is not None:
                out.append((reg, k - disp))
        return out

    def takes_address(self, address, insn):
        """Whether the instruction at address leaves a frame address in a register other than sp (a
        frame pointer, a pointer to a local); True where it cannot be told."""
        if not self.ok:
            return True
        state = self.states.get(address)
        if state is None or insn is None:
            return False                    # (no path reaches it)
        try:
            _, after = _STATES[id(self.program)].step(insn, *state)
        except Exception:
            return True
        _, writes = self.arch.reg_effects(insn)
        return any(isinstance(after.get(r), tuple) and len(after[r]) == 2 and after[r][0] == "sp"
                   for r in writes if r != "sp")

    def addressed(self):
        """Whether an instruction a path reaches takes a frame address (True without states)."""
        if self._addressed is None:
            self._addressed = not self.ok or any(self.takes_address(a, self.program.insn(a)) for a in self.states)
        return self._addressed


SAVE_AREA_LEAD = 2      # the most instructions in front of a save area's push that write neither sp nor an
                        # argument register (APCS's mov ip, sp: pushed_save_area)


def pushed_save_area(program, insns):
    """Whether a function's first push stores argument registers only, the last of them among them (the
    arch layer's stack_kind and pushed_registers): a variadic function's save area as GCC's newlib
    keeps it (printf pushes r0-r3, sprintf r1-r3, before it saves anything else), which
    variadic_save_area does not find (it leaves pushes to the rows). The push is the first instruction,
    or follows at most SAVE_AREA_LEAD instructions that write neither sp nor an argument register and go
    on to the next (an APCS frame keeps the entry's stack pointer first: mov ip, sp; push {r1, r2, r3};
    push {r4, ip, lr}). insns: the function's instructions in order (None for a word that is none).
    False where the layer has no pushes. (dread.readable entry_save_areas: the functions so.)"""
    arch = program.arch
    if not (hasattr(arch, "stack_kind") and hasattr(arch, "pushed_registers")):
        return False
    args = list(program.target.abi.arg_regs)
    for insn in insns[:SAVE_AREA_LEAD + 1]:
        if insn is None:
            return False
        if arch.stack_kind(insn) == "push":
            regs = [r for r in arch.pushed_registers(insn) if r is not None]
            return bool(regs and args and set(regs) <= set(args) and args[-1] in regs)
        if not hasattr(arch, "reg_effects") or arch.flow(insn)[0] != "next" or \
                arch.reg_effects(insn)[1] & (set(args) | {"sp"}):
            return False
    return False


def save_area_reads(program, fn, insns, frames=None):
    """The argument registers a function pushes at its entry together with the last one (a variadic
    function's save area, which may hold named arguments too) and later loads back from their slots
    (Frames: through sp or any register that holds a frame address): those are read, by the load if
    not by the push. GCC's printf pushes r0-r3 and loads its format from r0's slot through a copy of
    sp; sprintf pushes r1-r3, its format in r1. The unnamed ones a va_list walks over are read through
    a pointer that moves (in a callee, mostly) and stay no use, as before (an assertion handler's
    callers would look as if they read r3). Needs the layer's pushed_registers and load_address;
    else, and without states, the empty set."""
    arch = program.arch
    if not insns or insns[0] is None or not hasattr(arch, "pushed_registers") or not hasattr(arch, "load_address"):
        return set()
    args = list(program.target.abi.arg_regs)
    pushed = arch.pushed_registers(insns[0])
    named = [r for r in pushed if r is not None]
    if not named or args[-1] not in named or not set(named) <= set(args):
        return set()
    total = arch.stack_bytes(insns[0])
    slots = {}                              # depth below the entry's stack pointer -> the register there
    for k, r in enumerate(pushed):
        if r is not None:
            slots[total - 4 * k] = r
    frames = frames if frames is not None else Frames(program, fn)
    if not frames.ok:
        return set()
    found = set()
    for a, insn in zip(fn.insns, insns):
        got = arch.load_address(insn) if insn is not None else None
        if got is None:
            continue
        base, disp = got
        k = frames.depth(a, base)
        if k is not None and k - disp in slots:
            found.add(slots[k - disp])
    return found


def _stores_on_entry(program, fn, insns, joins, reg):
    """Whether the entry's straight run stores reg (anywhere, not by a push) before it writes it."""
    arch = program.arch
    for a, insn in zip(fn.insns, insns):
        if insn is None or (a != fn.entry and a in joins):
            return False
        if arch.stack_kind(insn) != "push" and any(r == reg for r, _, _ in arch.store_slots(insn)):
            return True
        if reg in arch.reg_effects(insn)[1] or arch.flow(insn)[0] != "next":
            return False
    return False


def variadic_save_area(program, fn, insns, joins, frames=None):
    """The argument registers a variadic function stores in its frame on the entry's straight run
    before anything writes them: the run of stored ones that ends with the last argument register and
    has an argument register below it that is not stored (r1-r3 or a1-a7 behind one named argument;
    sprintf also keeps its first argument there, a use: its address goes to a callee); else the empty
    set. -O0 stores every argument register from the first on, and those stay uses. Where a store
    goes says Frames (through sp, a frame pointer or any copy: clang keeps r1-r3 with add r0, sp, #12;
    stm r0!, {r1, r2, r3}, clang 18 through its frame pointer), and each register of the area must lie
    right below the stack arguments (the last argument register a word below the entry's stack
    pointer, the one before it a word lower and so on, so that a va_list walks on into the stack
    arguments): FreeRTOS's xStreamBufferSend at GCC -O2 spills its fourth argument r3 at the entry,
    far below, and was taken for variadic, its r3 for no use (found by dread callconv). Pushes are
    left to the rows (a push is no use anyway). The empty set without states."""
    arch = program.arch
    if not hasattr(arch, "store_slots"):
        return set()
    args = list(program.target.abi.arg_regs)
    if not _stores_on_entry(program, fn, insns, joins, args[-1]):
        return set()                        # (cheap: no states for most functions)
    frames = frames if frames is not None else Frames(program, fn)
    if not frames.ok:
        return set()
    word = getattr(arch, "reg_bits", 32) // 8
    written, where = set(), {}
    for a, insn in zip(fn.insns, insns):
        if insn is None or (a != fn.entry and a in joins):
            break
        if arch.stack_kind(insn) != "push":
            for reg, depth in frames.stores(a, insn):
                if reg in args and reg not in written and reg not in where:
                    where[reg] = depth
        _, writes = arch.reg_effects(insn)
        written |= set(writes)
        if arch.flow(insn)[0] != "next":
            break
    if args[-1] not in where:
        return set()
    first = len(args) - 1
    while first > 0 and args[first - 1] in where:
        first -= 1
    area = set(args[first:]) if first > 0 else set()     # (all of them stored: -O0 keeping its arguments)
    if any(where[r] != word * (len(args) - args.index(r)) for r in area):
        return set()
    return area


def scratch_names(target, arch):
    """The registers a call may leave changed, which the analysis follows: the scratch registers,
    the flags (one by one, "flags:c" and so on, where the layer has flag_effects: Analysis folds them
    into "flags" in its result), and where floats travel in the FPU (hard float) its argument
    registers (s0-s15)."""
    callee_saved = set(target.abi.callee_saved)
    flags = ["flags:" + n for n in arch.FLAG_NAMES] if hasattr(arch, "flag_effects") else ["flags"]
    return [r for r in arch.general_regs() if r not in callee_saved] + flags + list(target.abi.fp_arg_regs)


POOL_REACH = 0x1000         # how far behind a function's last instruction its pool may lie
POINTER_CALLERS = "callers through a pointer"   # the site that stands for them in sites (why: "read after
                                                # the call by callers through a pointer (its result)")


VECTORS_MAX = 512           # entries an M-profile vector table has at most (16 for the core, 496 interrupts)


def vector_words(program, entries):
    """The addresses of the words of an M-profile vector table (the image's vectors: the stack pointer's
    word, then the handlers' entries with bit 0 set, or 0 for a reserved one, up to the first word that is
    neither): the core enters a handler and discards its result (the exception's return puts back r0 as
    it was), so they are no pointers whose callers read a result (pointer_entries). Empty for other
    profiles: an A-profile vector holds an instruction (a handler's address in a literal pool counts)."""
    if getattr(program.arch, "profile", None) != "m":
        return set()
    base = program.image.vectors
    out = set()
    for k in range(1, VECTORS_MAX):
        a = base + 4 * k
        if not program.in_image(a, 4):
            break
        word = program.word(a)
        if word and not (word & 1 and program.code_address(word) in entries):
            break
        out.add(a)
    return out


def pointer_entries(program):
    """The names of the functions a pointer may call: those whose entry the image holds as an aligned
    word (in a literal pool, a table; a Thumb function's with bit 0 set or clear; not an M-profile vector
    table's: vector_words), whose address their code makes from the pc or from a pair of instructions
    (the layer's pc_address: adr, auipc or lui with addi behind it; wide_constants: movw with movt), or
    from another value it knows with other instructions between (made_pointers: GCC's RISC-V code puts
    a lui and its addi apart). Not the image's entry: the reset enters it and reads no result (a loader
    that does is not seen). A call through a register reads nothing for the analysis (its callee is not
    known), so these are the functions whose result a caller may read unseen (Analysis seeds their kept
    with the first result register). More than are called so (a word that only happens to equal an
    entry), and not every one: an address made by arithmetic the layers do not follow (an offset added
    to a table's base the code loads) is missed (not confirmed on any target)."""
    entries = {program.code_address(fn.entry): fn.name for fn in program.functions}
    found = set()
    data, arch = program.data, program.arch
    skip = (-program.base) % 4
    usable = (len(data) - skip) // 4 * 4
    table = vector_words(program, entries)
    for k, (word,) in enumerate(struct.iter_unpack("<I", bytes(data[skip:skip + usable]))):
        name = entries.get(program.code_address(word))
        if name is not None and program.base + skip + 4 * k not in table:
            found.add(name)
    pc_address = getattr(arch, "pc_address", None)
    wide = getattr(arch, "wide_constants", None)
    for fn in program.functions:
        insns = [program.insn(a) for a in fn.insns]
        made = list(wide(insns)) if wide is not None else []
        if pc_address is not None:
            made += [pc_address(insn, program.isa_at(insn.address)) for insn in insns if insn is not None]
        for value in made:
            if value is not None and program.code_address(value) in entries:
                found.add(entries[program.code_address(value)])
    return found | made_pointers(program, entries)


def made_pointers(program, entries):
    """The names of the functions whose entry (entries: address without bit 0 -> name) a function's code
    makes from another value it knows and uses as a pointer is used: stores it, hands it to a call or a
    tail call, returns it. The layer's value_step follows the values (an addi to the register a lui or
    an auipc set: GCC's RISC-V code puts other instructions between, which pc_address, joining
    neighbours, does not see: FreeRTOS's RISC-V GCC -Os demo makes 72 of its tasks' and callbacks'
    addresses so). Not a bare immediate (a count or a mask equals an entry in an image that starts at 0
    or at a round address: FreeRTOS's mps2 demos pass 140, which is strlen's entry, and RISC-V's lui of
    0x80000000 is its _start), nor a word loaded (pointer_entries reads the image's words). A walk in
    address order: a value lasts until something else writes its register, a call ends the scratch
    registers' values, a way out without a fall-through ends every value but the target's fixed
    registers'; more than the control flow allows where paths join (a function counted as called
    through a pointer that is not: its result then counts as read). Empty without a value_step."""
    arch = program.arch
    step = getattr(arch, "value_step", None)
    effects = getattr(arch, "reg_effects", None)
    if step is None or effects is None:
        return set()
    abi = program.target.abi
    fixed = {r: (v & 0xFFFFFFFF, False) for r, v in program.image.fixed.items()}
    keep = set(abi.callee_saved) | set(fixed)
    args, results = list(abi.arg_regs), list(abi.result)
    found = set()
    for fn in program.functions:
        inside = set(fn.insns)
        known, made = dict(fixed), set()        # made: registers whose value was made from another known

        def used(regs):
            for r in regs:
                v = known.get(r)
                if r in made and v is not None and not v[1] and program.code_address(v[0]) in entries:
                    found.add(entries[program.code_address(v[0])])
        for a in fn.insns:
            insn = program.insn(a)
            if insn is None:
                known, made = dict(fixed), set()
                continue
            kind, target, falls = arch.flow(insn)
            if kind in ("call", "icall"):
                used(args)
            elif kind == "return":
                used(results)
            elif kind == "ijump" or (kind == "jump" and isinstance(target, int) and target not in inside
                                     and program.code_address(target) not in inside):
                used(args)                      # (a tail call)
            try:
                values, _, stored = step(insn, program.isa_at(insn.address), known, program)
            except Exception:                   # (an instruction the layer's step does not take: no value)
                values, stored = {}, []
            used(stored)
            reads, writes = effects(insn)
            from_known = any(r in known and r != "zero" for r in reads)
            for r in writes:
                if r not in values:
                    known.pop(r, None)
                    made.discard(r)
            for r, v in values.items():
                if v is None or v[1]:
                    known.pop(r, None)
                    made.discard(r)
                else:
                    known[r] = v
                    if from_known:
                        made.add(r)
                    else:
                        made.discard(r)
            if kind in ("call", "icall"):
                known = {r: v for r, v in known.items() if r in keep}
                made &= set(known)
            elif kind in ("return", "jump", "ijump") and not falls:
                known, made = dict(fixed), set()
    return found


class Analysis:
    """The analysis of one program: per-instruction rows and control flow, what each function may
    write, and the solution (kept, reads). Kept in memory for explanations (readers()).

    A mask holds a field of the layer's reg_bits bits for each name (bit for bit: which bits of the
    register a read uses), bit[name] the whole field: a write ends a register whole, a read uses the
    bits its instruction's used results depend on (refinement). The result (RegFlow) names a register
    where any bit of its field is set."""

    def argument_pushes(self, fn):
        """The addresses of fn's pushes of a register into a word that a call takes as a stack argument,
        where the layer says pushes pass arguments (pushes_arguments: x86, whose cdecl pushes them):
        such a push reads its register (_rows), where other pushes read nothing. The words each direct
        call takes are its callee's named stack arguments (dread.lift's stack_args, over its frame
        states, which need no register flow): not call_words, which passes every word the call site
        stored to a callee that takes the address of one of its own (a va_list may walk on), and so
        took FreeRTOS's padding push ecx in front of a call of xQueuePeek for an argument; a variadic
        callee's unnamed words go unseen so. A push counts when it stored the word last on the
        straight run to the call. GCC's pushes of a
        dead register that pad the stack in front of the arguments (push eax; push eax; push $0x98e;
        push $0x10cb40; call vAssertCalled: two words taken) or only reserve a word lie outside what
        the call takes; a run that a join or a call starts anew forgets what was pushed before it (an
        argument pushed before a branch goes unseen: no read, as elsewhere here). Empty without the
        layer's word, or where the frame cannot be followed."""
        arch, prog = self.program.arch, self.program
        if not getattr(arch, "pushes_arguments", False):
            return set()
        from dread import lift
        if self._pusher is None:
            self._pusher = lift.make_lifter(prog, None)
        lifter = self._pusher
        try:
            states = lifter.states(fn, strict=False)
        except Exception:
            return set()
        joins = set()
        for a in fn.insns:
            insn = prog.insn(a)
            kind, target, _ = arch.flow(insn) if insn is not None else (None, None, None)
            if kind == "jump" and target is not None:
                joins.add(prog.code_address(target))
        out, writer = set(), {}
        for a in fn.insns:
            insn = prog.insn(a)
            if insn is None or a not in states:
                writer = {}
                continue
            if a in joins:
                writer = {}
            depth = states[a][0]
            kind, target, _ = arch.flow(insn)
            if arch.stack_kind(insn) == "push":
                regs = arch.pushed_registers(insn)
                if len(regs) == 1 and regs[0] is not None:
                    writer[depth + 4] = a
                else:                       # (memory or an immediate: no register read)
                    writer.pop(depth + 4, None)
            elif kind == "call" and target is not None:
                callee = prog.by_entry.get(prog.code_address(target))
                try:
                    n = lifter.stack_args(callee) if callee is not None else 0
                except Exception:
                    n = 0
                for k in range(n or 0):
                    if depth - 4 * k in writer:
                        out.add(writer[depth - 4 * k])
                writer = {}
            elif kind != "next":
                writer = {}
        return out

    def __init__(self, program):
        self.program = program
        arch = program.arch
        self.names = scratch_names(program.target, arch)
        self.width = getattr(arch, "reg_bits", 32)
        self.field = (1 << self.width) - 1
        self.shift = {n: self.width * i for i, n in enumerate(self.names)}
        self.bit = {n: self.field << k for n, k in self.shift.items()}
        flag_bits = [b for n, b in self.bit.items() if n.startswith("flags:")]
        if flag_bits:                       # "flags" stands for them all (the layer's and callers' name)
            self.bit["flags"] = sum(flag_bits)
        self.everything = (1 << (self.width * len(self.names))) - 1
        abi = program.target.abi
        self.result_reg = abi.result[0] if abi.result else None
        # a C function's result: the first result register, and with hard float the FPU's (s0, or s0:s1
        # for a double: the first two of fp_arg_regs). solve's comparison view passes them on at every
        # return; result takes them from the program's view
        self.results = self.mask([self.result_reg] + list(abi.fp_arg_regs[:2]))
        # the registers a call may leave its return address in (the layer's link_regs: RISC-V's ra and
        # t0): what the call writes there the callee reads from the call, not from the caller. Only
        # those: the MSP430 layer counts what a call clobbers as its writes, which the callee reads
        # from the caller first (its arguments)
        self.link_mask = sum(self.bit.get(r, 0) for r in getattr(arch, "link_regs", ()))
        entries = {fn.entry: fn.name for fn in program.functions}

        def entry_name(address):
            return entries.get(program.code_address(address)) if address is not None else None

        self.variadic = set()               # functions whose unnamed arguments a va_list reads (_rows)
        self._pusher = None                 # (argument_pushes: a lifter for the frames, made once)
        self.rows = {fn.name: _rows(program, fn, self, entry_name) for fn in program.functions}
        ordered = sorted(fn.entry for fn in program.functions)
        following = {a: b for a, b in zip(ordered, ordered[1:])}
        self.leaves = {}                    # name -> the rows that may go where the analysis cannot follow
        self.graphs = {fn.name: self._graph(fn, self.rows[fn.name], entry_name,
                                            following.get(fn.entry, program.end(fn)))
                       for fn in program.functions}
        self.edges = {name: self._edge_masks(self.rows[name], self.graphs[name][0], program.arch) for name in self.rows}
        self.shadowed = {}                  # name -> {call index: registers not read behind it} (_neutral_saves)
        self.copies = {}                    # name -> {save index: (restore index, register bit)} (_neutral_saves)
        for fn in program.functions:
            self._neutral_saves(fn)
        self._saved_reads = self._callee_saved_reads()
        for fn in program.functions:
            self._dead_saved_writes(fn)
        self.may = self._may_write()
        self._must = None
        self._variadic_sites(program)
        self.handed = self._handed()
        # a runtime helper's result by the ABI is read, whatever the original's callers do with it:
        # compiled C calls the helper by that contract (the layer's abi_results)
        abi = getattr(arch, "abi_results", None)
        compiler = getattr(program.target, "compiler", None)
        measured = compiler.profile_results() if compiler is not None else {}      # dread cclab
        self.abi, self.abi_label = {}, {}
        for name in self.rows:
            by_abi = self.mask(abi(name)) if abi is not None else 0
            by_profile = self.mask(measured.get(name, ()))
            if by_abi | by_profile:
                self.abi[name] = by_abi | by_profile
                self.abi_label[name] = {reg: "the ABI" if by_abi & self.bit[reg] else "the compiler's profile"
                                        for reg in self.names if (by_abi | by_profile) & self.bit[reg]}
        # a function whose entry the image holds or its code makes may be called through a pointer, by
        # callers the analysis does not see: its result counts as read (pointer_entries)
        self.pointers = pointer_entries(program) if self.result_reg in self.bit else set()
        self.seed = 0                       # what every return passes on besides kept (solve: compared)
        self.kept = self.reads = None
        self._start()
        self._final = {}                    # name -> (uses as the solution has them, kills, exits), after solve

    def _start(self):
        """kept and reads as a solution starts from: the ABI's and the compiler profile's results, and the
        result of every function a pointer may call."""
        result = self.result_reg
        self.kept = {name: self.abi.get(name, 0) | (self.bit[result] if name in self.pointers else 0)
                     for name in self.rows}
        self.reads = {name: 0 for name in self.rows}

    def mask(self, regs):
        m = 0
        for r in regs:
            m |= self.bit.get(r, 0)
        return m

    def registers(self, m):
        """The names of the registers of which mask m has a bit, in bit order."""
        return [n for n in self.names if m & self.bit[n]]

    def compress(self, m):
        """Mask m with one bit for each name whose field has a bit (the RegFlow's masks)."""
        out = 0
        for i, n in enumerate(self.names):
            if m & self.bit[n]:
                out |= 1 << i
        return out

    def refinement(self, insn, reads, writes, conditional, used):
        """What of a row's uses (used: the registers it reads, as a mask) counts, beyond reading them at
        all: a function of what is used of its results after it, a mask, or None (all of it).
          - An instruction that only computes registers (the layer's register_only), all of which the
            analysis follows (scratch registers, flags), or any where the layer says refine_saved_writes
            (x86 moves bytes through ebx and esi: mov bl, cl; a register not followed counts as all
            used): its results are all it does, so it reads only
            what its used results need (a register whose result nobody uses reads nothing), bit for bit
            where the layer has a rule (bit_demand: bic r1, r1, #0xff whose result's low byte alone is
            used reads none of r1; lsl #16 reads the low half), else every bit once any result bit is
            used; every flag it reads where it runs under a condition (the condition decides whether
            the result is written), every bit where a flag it sets is used.
            Where a flag it sets is used, the bits the flags depend on where the layer has a rule
            (flag_demand: x86's cmp dl, 0x20 reads the low byte of edx), else every bit.
          - A store of a byte or a halfword (the layer's store_demand): those bits of the register.
          - A write of part of a register that keeps the rest, from memory (the layer's merge_demand:
            x86's mov dl, byte ptr [eax]): of that register the bits it computes from, and the kept bits
            as far as they are used behind it (GCC's x86 code loads bytes so into registers it reads
            no more of: every such function looked as if it read them at its entry).
        A read of bits nobody uses is no read: until regflow 26 every read counted, and a register
        whose upper bits a caller kept through a bic and an orr but stored nowhere looked read after
        the call (what its callee left there a second result)."""
        arch = self.program.arch
        only = getattr(arch, "register_only", None)
        if only is not None and writes and only(insn) and (all(w in self.bit for w in writes) or
                                                           getattr(arch, "refine_saved_writes", False)):
            return self._computed(insn, writes, conditional, used)
        store = getattr(arch, "store_demand", None)
        got = store(insn) if store is not None else None
        if got:
            keep = self.everything
            for r, bits in got.items():
                if r in self.shift:
                    keep = (keep & ~self.bit[r]) | ((bits & self.field) << self.shift[r])
            return keep if keep != self.everything else None
        merge = getattr(arch, "merge_demand", None)
        got = merge(insn) if merge is not None else None
        if got:
            return self._merged(got, used)
        return None

    def _merged(self, merges, used):
        """refinement's function for a write of part of a register that keeps the rest (merges: {register:
        (kept, operand)}, the layer's merge_demand): live -> the bits of `used` it reads."""
        field, shift, bit = self.field, self.shift, self.bit
        parts = [(bit[r], shift[r], kept & field, operand & field) for r, (kept, operand) in merges.items()
                 if r in shift]

        def demand(live):
            got = used
            for b, k, kept, operand in parts:
                got = (got & ~b) | ((((live >> k) & kept) | operand) << k)
            return got & used
        return demand

    def _computed(self, insn, writes, conditional, used):
        """refinement's function for an instruction that only computes registers: live (the mask of what
        is used behind it) -> the bits of `used` it reads."""
        precise = getattr(self.program.arch, "bit_demand", None)
        flagged = getattr(self.program.arch, "flag_demand", None)
        outs = [(w, self.shift[w]) for w in sorted(writes) if w in self.shift]
        flags = [w == "flags" or w.startswith("flags:") for w, _ in outs]
        under = used & self.bit.get("flags", 0) if conditional else 0
        field, shift, memo = self.field, self.shift, {}
        # a register the analysis does not follow (callee-saved; refinement lets such a write through
        # where the layer says refine_saved_writes): its bits count as all used
        fixed = {w: field for w in writes if w not in shift}

        def demand(live):
            key = tuple((live >> k) & field for _, k in outs)
            got = memo.get(key)
            if got is None:
                flag_used = any(v for v, flag in zip(key, flags) if flag)
                rest = {w: v for (w, _), v, flag in zip(outs, key, flags) if not flag}
                rest.update(fixed)
                want = None
                if not any(key) and not fixed:
                    got = 0
                elif precise is not None and (not flag_used or flagged is not None):
                    # (where a flag it sets is used: the bits those depend on, the layer's flag_demand
                    # (x86's cmp dl, 0x20 reads the low byte of edx); without that rule every bit)
                    want = precise(insn, rest) if any(rest.values()) else {}
                    if want is not None and flag_used:
                        more = flagged(insn)
                        want = None if more is None else {r: want.get(r, 0) | more.get(r, 0)
                                                          for r in set(want) | set(more)}
                if got is None:
                    if want is None:
                        got = used
                    else:
                        got = under
                        for r, bits in want.items():
                            if r in shift:
                                got |= (bits & field) << shift[r]
                        got &= used
                memo[key] = got
            return got
        return demand

    @staticmethod
    def _edge_masks(code, succ, arch=None):
        """{(branch row, successor row): registers written on that edge}: a conditional branch's taken
        edge carries what was written under its condition since the flags last changed (it eq; ldreq
        r3, [r1]; beq L: r3 is written at L by that way), its fall-through edge what was written under
        the opposite condition. Where both ways lead to the same row, neither (the write is not
        certain there)."""
        index = {row.address: i for i, row in enumerate(code)}
        out = {}
        for i, row in enumerate(code):
            if row.kind != "jump" or not row.conditional or not (row.covered or row.fall_covered):
                continue
            taken = index.get(code_address(arch, row.target or 0))
            fall = i + 1 if i + 1 < len(code) and code[i + 1].address == row.address + row.size else None
            if taken is None or taken == fall:
                continue
            if row.covered and taken in succ[i]:
                out[(i, taken)] = row.covered
            if row.fall_covered and fall is not None and fall in succ[i]:
                out[(i, fall)] = row.fall_covered
        return out

    def _graph(self, fn, code, entry_name, span_end):
        """(successors, returns, calls [(index, callee or None)], tails [(index, callee)], unknown):
        the control flow inside one function. unknown: it may go where the analysis cannot follow
        (an indirect call or tail call, a branch into other code). Indirect jumps: see
        _switch_successors. The rows from which the way may leave the function for code the analysis
        does not follow (a branch into other code, an indirect jump with a target it does not know)
        go into self.leaves[fn.name] (_must_of counts them as returns)."""
        n = len(code)
        index = {row.address: i for i, row in enumerate(code)}
        succ = [[] for _ in range(n)]
        returns, calls, tails, ijumps, unknown = [], [], [], [], False
        leaves = self.leaves.setdefault(fn.name, set())
        # a call through a register that holds one function's entry (mov #f, r10; call r10): a call
        # of f, whose result the code behind it may read (gizmo-msp430's parse_uint reads r13 of
        # __mspabi_mpyl so; missed, the multiply was lifted with a 16-bit result and accepted)
        through = getattr(self.program.arch, "register_call_targets", None)
        resolved = through(self.program, fn) if through is not None else {}

        def nxt(i):
            return i + 1 if i + 1 < n and code[i + 1].address == code[i].address + code[i].size else None

        def falls_into(i):
            return nxt(i) is not None and (code[i].kind in ("next", "call", "icall") or code[i].falls)

        for i, row in enumerate(code):
            kind, target = row.kind, row.target
            if kind == "jump":
                t = self.program.code_address(target)
                if t in index:
                    succ[i].append(index[t])
                elif entry_name(t):
                    tails.append((i, entry_name(t)))
                else:
                    unknown = True          # into the middle of other code: passes nothing on
                    leaves.add(i)
            elif kind == "call":
                callee = entry_name(target)
                calls.append((i, callee))   # None: not a function's entry, treated as indirect
                unknown |= callee is None
            elif kind == "icall":
                targets = resolved.get(row.address, ())
                callee = entry_name(targets[0]) if len(targets) == 1 else None
                calls.append((i, callee))
                unknown |= callee is None
            elif kind == "return":
                returns.append(i)
            elif kind == "ijump":
                prev = code[i - 1] if i else None
                if target and prev is not None and prev.stack == "pop" and target in prev.written:
                    returns.append(i)       # pop {rN}; bx rN: a return (interworking on ARMv4T)
                else:
                    ijumps.append(i)
            if kind in ("next", "call", "icall") or row.falls:
                if nxt(i) is not None:
                    succ[i].append(nxt(i))
                elif kind == "next" and entry_name(row.address + row.size):
                    tails.append((i, entry_name(row.address + row.size)))     # falls into the next function
        if ijumps:
            unknown |= self._switch_successors(fn, code, index, ijumps, succ, falls_into, span_end, tails,
                                               entry_name, leaves)
        return succ, returns, calls, tails, unknown

    def _switch_successors(self, fn, code, index, ijumps, succ, falls_into, span_end, tails, entry_name,
                           leaves=None):
        """Give each indirect jump its targets; True when one is left without, or with a target
        outside the function that is no function's entry (the analysis cannot follow it): those go
        into leaves (also one given 4.'s roots: the jump may go elsewhere). In this order:
          0. the code in front of the jump run in an emulator (dread.switches), for every index
             value its bound check lets through, or twice for a jump without one: targets in the
             function are a switch's cases, targets elsewhere tail calls (a veneer, a far jump);
          1. a table right behind the instruction (arch.switch_targets: tbb/tbh, ldr pc, add pc);
          2. a table of addresses of the function's own instructions that lies between its entry
             and the next function (in a pool), whose address one of the eight instructions before
             the jump loads or computes;
          3. all such tables of the function;
          4. the code no path reaches: first the unreached blocks no direct edge leads to (switch
             cases are reached only through the table), then one root per region still unreached,
             in address order. Not every unreached block: a loop inside a switch case would be
             entered past the instructions that set it up, and what they set up would look like
             the caller's.
        """
        program, arch = self.program, self.program.arch
        n = len(code)

        def inside(address):
            return program.code_address(address) in index

        # Tables lie in the function's own extent or in the pool behind it: up to the next function,
        # but no further than POOL_REACH behind the last instruction (the next entry may be far away,
        # code the startup copies to RAM), read in one piece.
        import struct
        lo = fn.entry & ~3
        hi = min(span_end, program.end(fn) + POOL_REACH)
        try:
            blob = program.read(lo, max(0, hi - lo) & ~3)
        except Exception:
            blob = b""
        words = struct.unpack("<%dI" % (len(blob) // 4), blob)
        tables, run = {}, None
        for k, word in enumerate(words):
            a = lo + 4 * k
            if a in program.code or a + 2 in program.code:
                word = None
            if word is not None and inside(word):
                if run is None:
                    run = a
                    tables[run] = []
                tables[run].append(program.code_address(word))
            else:
                run = None

        def table_at(value):
            for start, targets in tables.items():
                if start <= value < start + 4 * len(targets):
                    return targets[(value - start) // 4:]
            return None

        from dread import switches
        left, lost = [], False
        for i in ijumps:
            insn = program.insn(code[i].address)
            resolved = switches.resolve(program, fn, code[i].address)
            targets = resolved[0] if resolved else None
            if resolved and resolved[1]:
                for t in resolved[1]:
                    if entry_name(t):
                        tails.append((i, entry_name(t)))
                    else:
                        lost = True
                        if leaves is not None:
                            leaves.add(i)
                if not targets:
                    continue
            if not targets and insn is not None:
                targets = arch.switch_targets(insn, program, inside)
            if not targets:
                for back in range(max(0, i - 8), i):
                    near = program.insn(code[back].address)
                    if near is None:
                        continue
                    isa = program.isa_at(near.address)
                    lit = arch.literal(near, isa)
                    value = arch.pc_address(near, isa)
                    if lit and lit[1] == 4 and program.in_image(lit[0], 4):
                        value = program.word(lit[0])
                    found = table_at(program.code_address(value)) if value is not None else None
                    if found:
                        targets = found
            if not targets and tables:
                targets = [t for ts in tables.values() for t in ts]
            if targets:
                succ[i].extend(sorted({index[program.code_address(t)] for t in targets if inside(t)}))
            else:
                left.append(i)
                if leaves is not None:
                    leaves.add(i)
        if not left:
            return lost
        seen, roots = set(), []

        def mark(j):
            stack = [j]
            while stack:
                k = stack.pop()
                if k not in seen:
                    seen.add(k)
                    stack.extend(succ[k])
        if n:
            mark(0)
        # Switch cases are reached only through the table: first the unreached blocks that no
        # direct edge leads to (a loop body inside a case has its back edge), then in address order
        # whatever is still unreached.
        entered = set()
        for i in range(n):
            entered.update(succ[i])
        for pass_ in ("no predecessor", "any"):
            for j in range(n):
                if j in seen or (j and falls_into(j - 1) and j - 1 not in seen):
                    continue
                if pass_ == "no predecessor" and j in entered:
                    continue
                roots.append(j)
                mark(j)
        for i in left:
            succ[i].extend(roots)
        return lost or not roots

    SAVE_RUN = 64                       # instructions from a save to its restore at most

    def _neutral_saves(self, fn):
        """A scratch register stored in the frame while it still holds its value from the entry on every
        path, and loaded back from that slot behind a call, all on one straight run, is neither read by
        the store nor written by the load: GCC saves a register it believes live around a call so, also
        where nothing has set it (FreeRTOS's tick on RISC-V saved t1 around its assertion call, and 135
        functions looked as if their callers read t1). Every load of the slot must be the restore of such
        a pair (GCC reuses a slot for several registers' saves), no frame address may be taken on the
        run, and an argument register only counts in a function that takes no frame address at all
        (g(&a) keeps an argument so for a callee to read)."""
        arch = self.program.arch
        code = self.rows[fn.name]
        if not hasattr(arch, "slot_access") or not code:
            return
        insns = [self.program.insn(row.address) for row in code]
        raw = [arch.slot_access(i) if i is not None else None for i in insns]
        if not {s[1] for s in raw if s and s[0] == "store" and self.bit.get(s[1])} & \
                {s[1] for s in raw if s and s[0] == "load"}:
            return                          # (cheap: no states for most functions)
        frames = Frames(self.program, fn)
        if not frames.ok:
            return
        succ = self.graphs[fn.name][0]
        args = set(self.program.target.abi.arg_regs)
        slots = [frames.access(row.address, i) for row, i in zip(code, insns)]  # (by depth: any base)
        preds = [0] * len(code)
        for i in range(len(code)):
            for j in succ[i]:
                preds[j] += 1
        pairs = []                          # (save, restore, register, slot) on one run around a call
        for i, s in enumerate(slots):
            if not s or s[0] != "store" or not self.bit.get(s[1]):
                continue
            reg, offset, bit = s[1], s[2], self.bit[s[1]]
            j, called = i, False
            for _ in range(self.SAVE_RUN):
                if len(succ[j]) != 1 or preds[succ[j][0]] != 1:
                    break                   # a branch, or something joins in
                called = called or code[j].kind in ("call", "icall")
                j = succ[j][0]
                if slots[j] == ("load", reg, offset):
                    if called:
                        pairs.append((i, j, reg, offset))
                    break
                if code[j].writes & bit or "sp" in code[j].written or (slots[j] and slots[j][2] == offset) or \
                        frames.takes_address(code[j].address, insns[j]):
                    break
        restores = {j for _, j, _, _ in pairs}
        held = {s[2] for k, s in enumerate(slots) if s and s[0] == "load" and k not in restores}
        pairs = [p for p in pairs if p[3] not in held]      # a slot another load reads holds a value
        restored = {j: (i, self.bit[reg]) for i, j, reg, _ in pairs}
        start = next((i for i, row in enumerate(code) if row.address == fn.entry), None)
        if start is None or not pairs:
            return
        # Forward: the registers that may be written on some path to each row (a call writes every
        # scratch register; a restore puts back what its save found)
        before = [None] * len(code)
        before[start], work = 0, [start]
        while work:
            i = work.pop()
            out = before[i] | code[i].writes | (self.everything if code[i].kind in ("call", "icall") else 0)
            if i in restored:
                save, bit = restored[i]
                out = (out & ~bit) | ((before[save] or 0) & bit)
            for j in succ[i]:
                new = out if before[j] is None else before[j] | out
                if new != before[j]:
                    before[j] = new
                    work.append(j)
        for i, j, reg, offset in pairs:
            bit = self.bit[reg]
            if before[i] is None or before[i] & bit or (reg in args and frames.addressed()):
                continue
            # A copy through the slot: the save reads the register only where something reads the
            # restored value (liveness: what is live behind the restore is live in front of the save),
            # and the restore writes it within the function (a callee between may have changed it:
            # newlib's __malloc_lock writes r0, which _free_r saves around it and stores errno through
            # after). For the function's callers the restore puts back what the save found: no write.
            code[i] = code[i]._replace(uses=code[i].uses & ~bit)
            code[j] = code[j]._replace(writes=code[j].writes & ~bit)
            self.copies.setdefault(fn.name, {})[i] = (j, bit)
            # The restore passes the entry's value on, so the register looked live behind the calls
            # the pair encloses, as if their callers read what the callee left there (FreeRTOS on
            # RISC-V saves a2 around its assertion call: 23 functions refused for reading a2 behind
            # vAssertCalled). Behind such a call the restore replaces the callee's value before
            # anything reads it: not read behind that call.
            path, k = [i], i
            while k != j:
                k = succ[k][0]
                path.append(k)
            for n, c in enumerate(path[:-1]):
                if code[c].kind in ("call", "icall") and not any(code[q].uses & bit for q in path[n + 1:-1]):
                    shadow = self.shadowed.setdefault(fn.name, {})
                    shadow[c] = shadow.get(c, 0) | bit

    def _saved_liveness(self, fn, summary, reads_only=False):
        """(live after each row, live at the entry, the rows' (reads, writes)) of the callee-saved
        registers inside fn, as bit masks over _saved_names: live where it returns or tail-calls (its
        caller has them back), used at a call as far as the callee reads them from its caller
        (summary; all of them for a call through a register), ended by a write not under a condition.
        A push is no use (it saves them, as regflow takes pushes of scratch registers). reads_only:
        what fn reads of its caller's (_callee_saved_reads): a return passes nothing on, and a store
        of one into the frame is no use either (a save: RISC-V's sw s0, 8(sp)). None for a function
        that may go where the analysis cannot follow."""
        arch, code = self.program.arch, self.rows[fn.name]
        succ, returns, calls, tails, unknown = self.graphs[fn.name]
        if unknown or not code:
            return None
        bit = self._saved_bit
        every = (1 << len(bit)) - 1
        access = getattr(arch, "slot_access", None) if reads_only else None
        frames = None
        n = len(code)
        uses, kills, effects = [every] * n, [0] * n, [None] * n
        for i, row in enumerate(code):
            insn = self.program.insn(row.address)
            if insn is None:
                continue
            reads, writes = arch.reg_effects(insn)
            effects[i] = (reads, writes)
            slot = access(insn) if access is not None else None
            save = slot is not None and slot[0] == "store" and slot[1] in bit
            if save and slot[2] != "sp":        # (through a frame pointer or another copy: Frames)
                frames = frames or Frames(self.program, fn)
                save = frames.depth(row.address, slot[2]) is not None
            uses[i] = 0 if row.stack == "push" or save else sum(bit.get(r, 0) for r in reads)
            kills[i] = 0 if row.conditional else sum(bit.get(r, 0) for r in writes)
        exits = [0] * n
        if not reads_only:
            for i in returns:
                exits[i] = every
            for i, _ in tails:
                exits[i] = every
        for i, callee in calls:
            uses[i] |= summary.get(callee, every) if callee else every
        live_in, live_out = [0] * n, [0] * n
        changed = True
        while changed:
            changed = False
            for i in range(n - 1, -1, -1):
                o = exits[i]
                for j in succ[i]:
                    o |= live_in[j]
                v = uses[i] | (o & ~kills[i])
                if o != live_out[i] or v != live_in[i]:
                    live_out[i], live_in[i], changed = o, v, True
        return live_out, live_in[0], effects

    def _callee_saved_reads(self):
        """{name: the callee-saved registers fn reads from its caller} (bit masks over _saved_names),
        all functions together: an ordinary function saves what it uses and reads none; a compiler's
        shared code reads its caller's (IAR's ?Subroutine0: mov r1, r6). A function that may go where
        the analysis cannot follow reads all of them."""
        saved = [r for r in self.program.target.abi.callee_saved if r != "sp"]
        self._saved_names = saved
        self._saved_bit = {r: 1 << k for k, r in enumerate(saved)}
        every = (1 << len(saved)) - 1
        summary = {name: 0 for name in self.rows}
        changed = True
        while changed:
            changed = False
            for fn in self.program.functions:
                got = self._saved_liveness(fn, summary, reads_only=True)
                new = every if got is None else got[1]
                if new != summary[fn.name]:
                    summary[fn.name], changed = new, True
        return summary

    def _dead_saved_writes(self, fn):
        """An instruction that only computes callee-saved registers (the layer's register_only: no
        memory, no branch) whose results nothing reads before the function puts them back reads none of
        its registers. GCC 10 on RISC-V copies a register nothing set into s0 and never reads s0 again
        (FreeRTOS's xTaskCheckForTimeOut: c.mv s0, a4 behind its critical section's entry), and a4
        seemed an input of it, and through it of vAssertCalled's callers: 13 functions of that build
        refused for registers their callers seemed to read. The callee-saved registers are followed
        inside the function (_saved_liveness), a call using what its callee reads from its caller
        (_callee_saved_reads)."""
        arch = self.program.arch
        only = getattr(arch, "register_only", None)
        code = self.rows[fn.name]
        if only is None or not code or not self._saved_bit:
            return
        succ = self.graphs[fn.name][0]
        reached = {j for ss in succ for j in ss} | {0}
        if len(reached) < len(code):
            # code no edge leads to: a switch whose cases were not all found (GCC 12's RISC-V
            # xTaskGenericNotifyFromISR: one case of five), so what is live there would be lost
            return
        got = self._saved_liveness(fn, self._saved_reads)
        if got is None:
            return
        live_out, _, effects = got
        saved, bit = set(self._saved_names), self._saved_bit
        insns = [self.program.insn(row.address) for row in code]
        for i, (row, insn) in enumerate(zip(code, insns)):
            if insn is None or effects[i] is None or row.kind != "next" or not only(insn):
                continue
            written = set(effects[i][1])
            if not written or written - saved:
                continue                    # it writes something else too (a scratch register, the flags)
            if live_out[i] & sum(bit[r] for r in written):
                continue
            code[i] = code[i]._replace(uses=0)

    def _variadic_sites(self, program):
        """The calls of variadic functions and what their formats say (dread.varargs). formats: {address:
        the argument registers the call or tail call there passes, as a mask} where its format is known
        (effects counts them as the call's uses, _handed leaves the call alone). variadic_all: the
        functions variadic by their save area (variadic; a push of argument registers at the entry,
        pushed_save_area) or that take a format (dread.varargs family). format_sites: {caller: [[address,
        callee, how many argument registers it passes, the format's address], ...]} of every call and tail
        call of one of them, the last two None where the format is not known (dread.readable: a function
        whose calls of them are all known reads what regflow says; a call whose format argument is one of
        the formats passes what it takes)."""
        from dread import varargs
        args = [r for r in program.target.abi.arg_regs if r in self.bit]
        taking = {n: i for n, i in varargs.family(program.target).items() if n in self.rows}
        pushed = {fn.name for fn in program.functions
                  if pushed_save_area(program, [program.insn(a) for a in fn.insns[:SAVE_AREA_LEAD + 1]])}
        self.variadic_all = set(self.variadic) | pushed | set(taking)
        found = []                          # (dread.varargs sites, from the graphs: a branch, not a fall)
        for fn in program.functions:
            code = self.rows[fn.name]
            _, _, calls, tails, _ = self.graphs[fn.name]
            here = [(code[i].address, c, "call") for i, c in calls if c in self.variadic_all]
            here += [(code[i].address, c, "tail") for i, c in tails
                     if c in self.variadic_all and code[i].kind == "jump"]
            found += [(fn, a, c, kind) for a, c, kind in sorted(here)]
        arities = varargs.call_arities(program, taking, args, found) if taking and args else {}
        self.formats = {a: self.mask(args[:n]) for a, (n, _) in arities.items()}
        self.format_sites = {}
        for fn, address, callee, _ in found:
            n, fmt = arities.get(address, (None, None))
            self.format_sites.setdefault(fn.name, []).append([address, callee, n, fmt])

    def _described_syscall(self, row):
        """Whether a row is a system call whose handler's reads the target states ([abi] syscall_reads):
        it reads those (_rows) and nothing else, so it is no sink for _handed."""
        if getattr(self.program.target.abi, "syscall_reads", None) is None:
            return False
        insn = self.program.insn(row.address)
        return insn is not None and getattr(self.program.arch, "is_syscall", lambda i: False)(insn)

    def _handed(self):
        """{name: {row: registers}}: what counts as read at the rows where a value goes to code whose reads
        the analysis does not see: a call through a pointer (or an svc, an ecall), a call or tail call of
        a variadic function (its unnamed arguments, which a va_list reads, are no use in its save area),
        a way out the analysis cannot follow (self.leaves). An argument register there whose value the
        function computed itself is read where it was computed: those instructions read their operands
        whatever is used behind them (their demand is dropped: _rows' refinement would take their result
        for unused); one that holds a callee's result on every path counts as read at the row, so that
        the callee's result is used. A register that holds what the function got from its caller on some
        path, or what a callee merely left there, reads nothing there, as before regflow 26: counted, every
        callee in front of such a call looked as if its callers read the scratch registers it leaves
        alone, and a function as if it read every argument register (printf's save area)."""
        args = [r for r in self.program.target.abi.arg_regs if r in self.bit]
        entries = {fn.name: fn.entry for fn in self.program.functions}
        out = {}
        for name, (succ, _, calls, tails, _) in self.graphs.items():
            code = self.rows[name]
            unknown = self.variadic                 # (a call whose format is known passes what it says)
            sinks = [i for i, c in calls if (c is None and not self._described_syscall(code[i])) or
                     (c in unknown and code[i].address not in self.formats)]
            sinks += [i for i, c in tails if c in unknown and code[i].address not in self.formats]
            sinks += sorted(self.leaves.get(name, ()))
            if not sinks or not args:
                continue
            start = next((i for i, row in enumerate(code) if row.address == entries.get(name)), 0)
            preds = [[] for _ in code]
            for i, nexts in enumerate(succ):
                for j in nexts:
                    preds[j].append(i)
            call_at = dict(calls)
            for sink in sinks:
                for reg in args:
                    rows, by_call = self._definitions(code, preds, call_at, start, sink, self.bit[reg])
                    if by_call:
                        out.setdefault(name, {})[sink] = out.get(name, {}).get(sink, 0) | self.bit[reg]
                    for j in rows:
                        code[j] = code[j]._replace(demand=None)
        return out

    def _definitions(self, code, preds, call_at, start, sink, bit):
        """(rows, by_call) of the register bit in front of row sink: the rows of the function that write it
        last on some path there; by_call where a callee's result writes it last on every path (a call of
        a function that writes it on every path, _must_write) and no path from the entry reaches the sink
        without a write; (set(), False) where some path brings what the function got from its caller or
        what a callee merely left there (a callee that may write it but not on every path)."""
        must = self._must_write()
        result = self.bit.get(self.result_reg, 0)
        rows, calls_seen, entry = set(), False, False
        seen, work = set(), list(preds[sink])
        if sink == start:
            return set(), False
        while work:
            j = work.pop()
            if j in seen:
                continue
            seen.add(j)
            row = code[j]
            if j in call_at:
                callee = call_at[j]
                if callee is None or self.may[callee] & bit:
                    if callee is None or not must[callee] & bit & result or row.conditional:
                        return set(), False         # (what a callee leaves there, or the caller's)
                    calls_seen = True
                    continue
            elif row.writes & bit:
                if row.stack == "pop":
                    return set(), False
                rows.add(j)
                if not row.conditional:
                    continue
            if j == start:
                entry = True
                continue
            work.extend(preds[j])
        if entry:
            return set(), False
        return rows, calls_seen and not rows

    def _may_write(self):
        """Registers each function may write, its callees included (least fixpoint)."""
        may = {}
        for name, code in self.rows.items():
            m = 0
            for row in code:
                m |= row.writes
            may[name] = self.everything if self.graphs[name][4] else m
        changed = True
        while changed:
            changed = False
            for name, (_, _, calls, tails, _) in self.graphs.items():
                m = may[name]
                for _, callee in calls + tails:
                    m |= may[callee] if callee else self.everything
                if m != may[name]:
                    may[name], changed = m, True
        return may

    def _callers(self):
        """{name: the functions that call it or branch to it}."""
        callers = collections.defaultdict(set)
        for name, (_, _, calls, tails, _) in self.graphs.items():
            for _, callee in calls + tails:
                if callee:
                    callers[callee].add(name)
        return callers

    def _must_write(self):
        """_must_sets, made once."""
        if self._must is None:
            self._must = self._must_sets()
        return self._must

    def _must_sets(self):
        """{name: the registers the function writes on every path from its entry to a return} (the
        first part of overwritten, the module's docstring), all functions together: each function's
        own analysis (_must_of) uses its callees' sets at its calls and tail calls, and a function
        whose set shrank sends its callers back into the queue. Every set starts at everything and only
        shrinks (a greatest fixpoint): a function that never returns, or only through itself, keeps
        everything, so that a call of it adds nothing to the paths that would go on behind it."""
        callers = self._callers()
        entries = {fn.name: fn.entry for fn in self.program.functions}
        must = {name: self.everything for name in self.rows}
        work = collections.deque(self.rows)
        queued = set(self.rows)
        while work:
            name = work.popleft()
            queued.discard(name)
            new = self._must_of(name, entries.get(name), must)
            if new != must[name]:
                must[name] = new
                for caller in callers[name]:
                    if caller not in queued:
                        queued.add(caller)
                        work.append(caller)
        return must

    def _must_of(self, name, entry, must, rows=False, results_only=False):
        """What one function writes on every path to a return, given its callees' sets (must): a
        forward analysis over its rows, what is written on every path to a row being the intersection
        of what its predecessors pass on (a conditional branch's edge adds what was written under its
        condition, _edge_masks), the entry's nothing. A row writes what it certainly writes (kills, a
        conditional write only where the opposite one completes it); a pop instead takes what it pops
        out of the set (it puts back what was pushed, the caller's own value where the push saved it:
        push {r0, lr}; bl f; pop {r0, pc} hands back the r0 its caller passed, whatever f wrote), and
        so does a restore _neutral_saves paired with its save; a call writes its link register (RISC-V's
        ra) and, when it is not conditional, what its callee writes on every path, not what the layer
        counts as clobbered (MSP430's r11-r15). What a way out passes on: a return or a branch to
        another function's code (a tail call, with what that one writes) taken under a condition adds
        what was written under it (Row.covered); a way the analysis cannot follow (self.leaves) and a
        row behind which no row follows that it knows (the end of the decoded code) count as returns.
        What a pop under a condition writes is never counted written under a condition (no compiler's
        code was seen to pop a scratch register so). A load that reads back a slot a push filled is a
        write here (as a push is no use to reads): compiled code reads no such slot before it writes it
        (a push of r0-r3 makes room for locals or keeps the stack aligned, and a variadic function's
        save area is kept from the trimming, readable.Context.unknown). rows: also what is written on
        every path to each row (in front of it). results_only: a call writes only its callee's first
        result register (what the function has its callee compute for it; the scratch registers a
        callee leaves changed are none of its values: _handed)."""
        code = self.rows[name]
        n = len(code)
        if not n:
            return (0, []) if rows else 0
        succ, returns, calls, tails, _ = self.graphs[name]
        every, link = self.everything, self.link_mask
        cond_popped = 0
        for row in code:
            if row.stack == "pop" and row.conditional:
                cond_popped |= row.writes
        gen, out_of = [0] * n, [0] * n         # what a row writes; what it puts back (a restore)
        for i, row in enumerate(code):
            if row.kind in ("call", "icall"):
                g = row.kills & row.writes & link
            elif row.stack == "pop":
                g, out_of[i] = 0, row.writes
            else:
                g = row.kills & row.writes
            gen[i] = g & ~cond_popped if row.conditional else g
        for i, (j, bit) in self.copies.get(name, {}).items():
            out_of[j] |= bit
        by_calls = self.bit.get(self.result_reg, 0) if results_only else self.everything
        for i, callee in calls:
            if callee is not None and not code[i].conditional:
                gen[i] |= must[callee] & by_calls
        edges = self.edges.get(name, {})
        preds = [[] for _ in range(n)]
        for i in range(n):
            for j in succ[i]:
                preds[j].append(i)
        start = next((i for i, row in enumerate(code) if row.address == entry), 0)
        ins, outs = [every] * n, [every] * n
        changed = True
        while changed:
            changed = False
            for j in range(n):
                v = 0 if j == start else every
                if j != start:
                    for p in preds[j]:
                        v &= outs[p] | (edges.get((p, j), 0) & ~cond_popped)
                o = (v & ~out_of[j]) | gen[j]
                if v != ins[j] or o != outs[j]:
                    ins[j], outs[j] = v, o
                    changed = True
        tail_at = collections.defaultdict(list)
        for i, callee in tails:
            tail_at[i].append(callee)
        returning, leaves = set(returns), self.leaves.get(name, set())
        result = every
        for i, row in enumerate(code):
            here = outs[i]
            if row.conditional and row.kind in ("return", "jump"):
                here |= row.covered & ~cond_popped      # (taken where the condition holds)
            if i in returning:
                result &= here
            for callee in tail_at.get(i, ()):
                result &= here | must[callee]
            if i in leaves:
                result &= outs[i]
            if not succ[i] and i not in returning and i not in tail_at and i not in leaves:
                result &= outs[i]
        return (result, ins) if rows else result

    def _reads_first(self, must):
        """{name: the registers whose value from its caller may reach what the function reads}: reads,
        but where reads ends a value that may still come back, this lets it flow on (overwritten must
        not take a register the caller's value reaches for the function's own): a call ends only what
        its callee writes on every path (must) and its link register, so that a value a callee hands
        back unwritten on some path reaches what the function reads behind the call; a call through a
        register (or to no function's entry) ends only its link register. As for reads, what a callee
        reached through a register reads is not known and is no use, and a push is no use (a pop puts
        a pushed value back, which _must_of sees; a load of a pushed slot, which compiled code does
        not read before it writes it, would not be seen). All functions together, from nothing up (a
        least fixpoint, like reads)."""
        callers = self._callers()
        entries = {fn.name: fn.entry for fn in self.program.functions}
        first = {name: 0 for name in self.rows}
        work = collections.deque(self.rows)
        queued = set(self.rows)
        while work:
            name = work.popleft()
            queued.discard(name)
            new = self._first_of(name, entries.get(name), must, first)
            if new != first[name]:
                first[name] = new
                for caller in callers[name]:
                    if caller not in queued:
                        queued.add(caller)
                        work.append(caller)
        return first

    def _first_of(self, name, entry, must, first):
        """What one function may read of its caller's registers, given its callees' (first) and what
        they write on every path (must): the liveness of liveness(own=True) with _reads_first's
        calls."""
        code = self.rows[name]
        n = len(code)
        if not n:
            return 0
        succ, _, calls, tails, _ = self.graphs[name]
        link = self.link_mask
        uses = [row.uses for row in code]
        kills = [row.kills for row in code]
        exits = [0] * n
        for i, callee in calls:
            row = code[i]
            linked = row.kills & link
            if callee is not None:
                uses[i] |= first[callee] & ~row.covered & ~(row.writes & link)
                kills[i] = linked if row.conditional else linked | must[callee]
            else:
                kills[i] = linked
        for i, callee in tails:
            exits[i] |= first[callee]
        edges = self.edges.get(name, {})
        copies = self.copies.get(name, {})
        live_in, live_out = [0] * n, [0] * n
        changed = True
        while changed:
            changed = False
            for i in range(n - 1, -1, -1):
                o = exits[i]
                for j in succ[i]:
                    o |= live_in[j] & ~edges.get((i, j), 0)
                v = uses[i] | (o & ~kills[i])
                if i in copies:             # a save: it reads what the restore's readers read (_neutral_saves)
                    j, bit = copies[i]
                    v |= live_out[j] & bit
                if o != live_out[i] or v != live_in[i]:
                    live_out[i], live_in[i], changed = o, v, True
        return live_in[next((i for i, row in enumerate(code) if row.address == entry), 0)]

    def effects(self, name, own=False):
        """(uses, kills, exits, demand) per instruction of a function with the current solution: calls use
        what the callee reads and end what it may write; returns and tail calls pass on what the
        function's callers read afterwards (exits). With own, returns pass nothing on: what the
        function itself reads, not what flows through it to its callers' later reads (a call site
        lets those through by not ending them). demand: the rows' refinements (Row.demand)."""
        code = self.rows[name]
        _, returns, calls, tails, _ = self.graphs[name]
        uses = [row.uses for row in code]
        kills = [row.kills for row in code]
        demand = [row.demand for row in code]
        exits = [0] * len(code)
        passed = 0 if own else self.kept[name] | self.seed
        for i in returns:
            exits[i] |= passed
        formats = self.formats
        for i, callee in tails:
            exits[i] |= self.reads[callee] | formats.get(code[i].address, 0) | (passed & ~self.may[callee])
        for i, callee in calls:
            if callee is not None:      # (the call writes its link register before the callee reads it)
                uses[i] |= (self.reads[callee] | formats.get(code[i].address, 0)) & ~code[i].covered & \
                    ~(code[i].writes & self.link_mask)
                kills[i] = 0 if code[i].conditional else self.may[callee]
            else:
                kills[i] = 0 if code[i].conditional else self.everything
        for i, args in self.handed.get(name, {}).items():
            uses[i] |= args
        return uses, kills, exits, demand

    def _live(self, name, uses, kills, exits, demand):
        """(live in, live out) per instruction: what is used before and behind each, bit for bit (a row's
        uses as far as its demand takes them from what is used behind it)."""
        succ = self.graphs[name][0]
        edges = self.edges.get(name, {})
        copies = self.copies.get(name, {})
        n = len(uses)
        live_in, live_out = [0] * n, [0] * n
        changed = True
        while changed:
            changed = False
            for i in range(n - 1, -1, -1):
                o = exits[i]
                for j in succ[i]:
                    o |= live_in[j] & ~edges.get((i, j), 0)
                u, d = uses[i], demand[i]
                if d is not None:
                    u &= d if d.__class__ is int else d(o)
                v = u | (o & ~kills[i])
                if i in copies:             # a save: it reads what the restore's readers read (_neutral_saves)
                    j, bit = copies[i]
                    v |= live_out[j] & bit
                if o != live_out[i] or v != live_in[i]:
                    live_out[i], live_in[i], changed = o, v, True
        return live_in, live_out

    def liveness(self, name, own=False):
        """(live at the entry, [(call index, callee, live after it)], tails). With own, live at the entry
        is what the function itself reads (_both)."""
        entry, own_entry, calls, tails = self._both(name)
        return (own_entry if own else entry), calls, tails

    def _both(self, name):
        """(live at the entry, what the function itself reads at its entry, [(call index, callee, live
        after it)], tails). What the function itself reads: its returns pass nothing on (a register that
        only flows through it to its callers' later reads is no read of its own), but each row reads what
        it reads with what is used behind it as its callers use it (the liveness that passes kept on): an
        instruction that computes the function's result reads its operands as far as the callers use the
        result."""
        _, _, calls, tails, _ = self.graphs[name]
        uses, kills, exits, demand = self.effects(name)
        live_in, live_out = self._live(name, uses, kills, exits, demand)
        if any(d is not None for d in demand):
            used = [u if d is None else u & (d if d.__class__ is int else d(o))
                    for u, d, o in zip(uses, demand, live_out)]
        else:
            used = uses
        own = self.effects(name, own=True)[2]
        own_in, _ = self._live(name, used, kills, own, [None] * len(used))
        shadow = self.shadowed.get(name, {})
        return (live_in[0] if uses else 0), (own_in[0] if uses else 0), \
            [(i, c, live_out[i] & ~shadow.get(i, 0)) for i, c in calls if c is not None], tails

    def final(self, name):
        """(uses, kills, exits) of a function with the solution, each row's uses as far as what is used
        behind it takes them (its demand): what read_always and readers walk. Made once a function
        (after solve)."""
        if name not in self._final:
            uses, kills, exits, demand = self.effects(name)
            _, live_out = self._live(name, uses, kills, exits, demand)
            used = [u if d is None else u & (d if d.__class__ is int else d(o))
                    for u, d, o in zip(uses, demand, live_out)]
            self._final[name] = (used, kills, exits)
        return self._final[name]

    def solve(self, compared=False):
        """The solution, from the start (_start): what each function's callers read after it (kept) and
        what it reads at its entry (reads), all functions together. compared: every return passes on the
        function's result (results: the first result register, with hard float the FPU's) besides what
        its callers read (seed), as the comparison reads the result register at every return
        (dread.runner compares it whether a caller reads it or not): a value that flows into a function's
        result is then used, so the callee that makes it keeps it (a caller that
        moves a callee's second register into its own result needs it whether its callers read that
        result or not). Without, the program's own view: the result counts where a caller's behaviour
        depends on it (or a pointer may call the function)."""
        self._start()
        self.seed = self.results if compared else 0
        self._final = {}
        callers = collections.defaultdict(set)
        for name, (_, _, calls, tails, _) in self.graphs.items():
            for _, callee in calls + tails:
                if callee:
                    callers[callee].add(name)
        work = collections.deque(self.rows)
        queued = set(self.rows)
        while work:
            name = work.popleft()
            queued.discard(name)
            _, entry_live, calls, tails = self._both(name)
            touched = []
            if entry_live != self.reads[name]:
                self.reads[name] = entry_live
                touched += callers[name]
            for _, callee, live in calls:
                if live & ~self.kept[callee]:
                    self.kept[callee] |= live
                    touched.append(callee)
            for _, callee in tails:
                passed = self.kept[name] | self.seed
                if passed & ~self.kept[callee]:
                    self.kept[callee] |= passed
                    touched.append(callee)
            for t in touched:
                if t not in queued:
                    queued.add(t)
                    work.append(t)
        self._final = {}
        return self

    def sites(self):
        """{callee: {register: [[caller, address, always]]}} of the current solution: the calls after which
        the register is read, and the tail calls that pass it on."""
        sites = collections.defaultdict(lambda: collections.defaultdict(list))
        for name, code in self.rows.items():
            _, calls, tails = self.liveness(name)
            for i, callee, live in calls:
                for reg in self.names:
                    if live & self.bit[reg]:
                        sites[callee][reg].append([name, code[i].address, self.read_always(name, i, reg)])
            for i, callee in tails:
                for reg in self.names:
                    if (self.kept[name] | self.seed) & self.bit[reg]:
                        sites[callee][reg].append([name, code[i].address, True])
        return sites

    def result(self, program=None):
        """The RegFlow of the current solution; program: (kept, sites) of the program's own view (solve
        without compared), whose result registers (results) they take: kept and sites of the comparison's
        view for every other register (what a function must hand back so that every comparison of its
        callers agrees), the program's for the result (whether any caller's behaviour depends on it:
        dread readable prints a function none reads void)."""
        sites = self.sites()
        kept = dict(self.kept)
        if program is not None and self.results:
            rbit = self.results
            program_kept, program_sites = program
            kept = {name: (kept[name] & ~rbit) | (program_kept[name] & rbit) for name in kept}
            for name in set(sites) | set(program_sites):
                regs = sites[name]
                for reg in self.registers(rbit):
                    regs.pop(reg, None)
                    if program_sites.get(name, {}).get(reg):
                        regs[reg] = program_sites[name][reg]
        for name, labels in self.abi_label.items():
            for reg, label in labels.items():
                sites[name][reg].append([label, 0, True])
        for name in sorted(self.pointers):
            sites[name][self.result_reg].append([POINTER_CALLERS, 0, False])
        preserved = {name: self.compress(kept[name] & ~self.may[name])
                     for name in self.rows if kept[name] & ~self.may[name]}
        must = self._must_write()
        first = self._reads_first(must)
        overwritten = {name: self.compress(must[name] & ~first[name]) for name in self.rows}
        flow = self._folded(self.names, {k: self.compress(v) for k, v in kept.items()},
                            {k: self.compress(v) for k, v in self.reads.items()},
                            {k: dict(v) for k, v in sites.items() if v}, preserved, overwritten)
        flow.leftover = sorted(self.leftovers())
        flow.computed = sorted(self.computed())
        flow.bits = {"kept": self.partial_bits(kept), "reads": self.partial_bits(self.reads)}
        flow.variadic = sorted(self.variadic_all)
        flow.formats = self.format_sites
        return flow

    def partial_bits(self, masks):
        """{name: {register: mask}} of the masks' registers of which fewer than all bits are set (general
        registers alone: a flag is read whole)."""
        out = {}
        for name, m in masks.items():
            got = {}
            for reg in self.names:
                if reg.startswith("flags"):
                    continue
                field = (m >> self.shift[reg]) & self.field
                if field and field != self.field:
                    got[reg] = field
            if got:
                out[name] = got
        return out

    def _folded(self, names, kept, reads, sites, preserved, overwritten=None):
        """The RegFlow, with flags followed one by one folded into "flags" (a flag read is the flags
        read: what the layers and the tools name); in overwritten, "flags" only where every flag is."""
        if not any(n.startswith("flags:") for n in names):
            return RegFlow(names, kept, reads, sites, preserved, overwritten)
        public = []
        for n in names:
            n = "flags" if n.startswith("flags:") else n
            if n not in public:
                public.append(n)
        moved = {1 << i: 1 << public.index("flags" if n.startswith("flags:") else n) for i, n in enumerate(names)}

        def fold(m):
            out = 0
            for bit, to in moved.items():
                if m & bit:
                    out |= to
            return out
        folded_sites = {}
        for callee, by_reg in sites.items():
            regs = {}
            for reg, entries in by_reg.items():
                key = "flags" if reg.startswith("flags:") else reg
                merged = {(e[0], e[1]): e for e in regs.get(key, [])}
                for e in entries:
                    k = (e[0], e[1])
                    merged[k] = [e[0], e[1], bool(merged[k][2] or e[2])] if k in merged else list(e)
                regs[key] = list(merged.values())
            folded_sites[callee] = regs
        flag_bits = sum(1 << i for i, n in enumerate(names) if n.startswith("flags:"))

        def fold_all(m):
            out = fold(m & ~flag_bits)
            if m & flag_bits == flag_bits:
                out |= 1 << public.index("flags")
            return out
        return RegFlow(public, {k: fold(v) for k, v in kept.items()}, {k: fold(v) for k, v in reads.items()},
                       folded_sites, {k: fold(v) for k, v in preserved.items() if fold(v)},
                       None if overwritten is None else {k: fold_all(v) for k, v in overwritten.items()})

    def leftovers(self):
        """The functions whose second result register (the target's [abi] result[1]: r1, a1) their
        callers read and that write it, their callees included (kept, not preserved: the lifted C returns
        two words), where what it holds at every return is a constant they put there or what a callee
        left there that is itself such a value (_origins): a value no C function returns, a scratch
        register a callee used and its caller hands on, as far as the machine code tells. Not a runtime
        helper whose result the ABI or the compiler's profile gives (abi), nor a function whose second
        register may hold its own computation, a value it loads, its caller's value (a path from its
        entry that writes none: a division's remainder where it divides by 0) or what a pointer's callee
        or a pop left there. A greatest fixpoint over the call graph: a function's callees decide whether
        what it hands on of theirs is a leftover. Not established: a real second result that is a
        constant on every path (a 64-bit function that returns a small number: its high word 0) counts
        as a leftover; a constant built by a pair of instructions (movw and movt) as the function's own
        computation."""
        abi = self.program.target.abi
        if len(abi.result) < 2 or abi.result[1] not in self.bit:
            return set()
        reg = abi.result[1]
        b = self.bit[reg]
        origins = {}
        for name in self.rows:
            if self.kept[name] & b and self.may[name] & b and name not in self.abi:
                got = self._origins(name, reg)
                if got is not None:
                    origins[name] = got
        left = set(origins)
        changed = True
        while changed:
            changed = False
            for name in sorted(left):
                if any(c not in left for c in origins[name]):
                    left.discard(name)
                    changed = True
        return left

    def computed(self):
        """The functions whose first result register holds at a return (or behind a tail call of a
        function that leaves it alone) a value one of their own instructions computed for it alone: an
        instruction that writes it and whose value nothing reads before it reaches the return (a strlen's
        count, a memcmp's difference), on some path. Not a callee's value (a call of a function that may
        write the register ends the search), not the caller's (a path from the entry), not one a pop puts
        back, and not a value the function also uses itself (a constant it stores through the register
        and leaves there: the register as a scratch register to the end). Such a function likely returns
        what its source computes, whether a caller reads it or not; dread readable keeps its result
        (printed void, its C would not compute the value, which the cases compare at the return: the gate
        turned such functions down, gizmo's memcmp and strlen among them)."""
        reg = self.result_reg
        if reg not in self.bit:
            return set()
        b = self.bit[reg]
        out = set()
        for fn in self.program.functions:
            code = self.rows[fn.name]
            if not code:
                continue
            succ, returns, calls, tails, _ = self.graphs[fn.name]
            start = next((i for i, row in enumerate(code) if row.address == fn.entry), 0)
            preds = [[] for _ in code]
            for i, nexts in enumerate(succ):
                for j in nexts:
                    preds[j].append(i)
            call_at = dict(calls)
            tail_at = dict(tails)
            exits = set(returns) | set(tail_at)

            def only_returned(d):
                """Whether nothing reads what row d writes into the register before a way out (a tail call
                of a function that reads it reads it: an argument)."""
                seen, work = set(), list(succ[d])
                while work:
                    j = work.pop()
                    if j in seen:
                        continue
                    seen.add(j)
                    row = code[j]
                    if row.uses & b and j not in exits:
                        return False
                    if j in tail_at and (self.reads[tail_at[j]] | self.formats.get(row.address, 0)) & b:
                        return False
                    if j in call_at:
                        callee = call_at[j]
                        if callee is None or (self.reads[callee] | self.formats.get(row.address, 0)) & b:
                            return False
                        if self.may[callee] & b and not row.conditional:
                            continue
                    elif row.kills & b:
                        continue
                    work.extend(succ[j])
                return True
            work = list(returns)
            for i, callee in tails:
                if not self.may[callee] & b and i != start:
                    work.extend(preds[i])
            seen = set()
            while work:
                i = work.pop()
                if i in seen:
                    continue
                seen.add(i)
                row = code[i]
                if i in call_at:
                    callee = call_at[i]
                    if callee is None or self.may[callee] & b:
                        if not row.conditional:
                            continue
                elif row.writes & b:
                    if row.stack != "pop" and only_returned(i):
                        out.add(fn.name)
                        break
                    if not row.conditional:
                        continue
                if i != start:
                    work.extend(preds[i])
        return out

    def _origins(self, name, reg):
        """The callees whose value of reg the function may hand back at its returns and tail calls, when
        every other value it may hand back there is a constant it writes itself (the layer's value_step
        knows it without any register's value but the target's fixed ones); None when it may hand back
        anything else: its own computation, a value loaded, its caller's (a path from its entry that
        writes none), what a call through a pointer or a pop left there, or where a way out cannot be
        followed (self.leaves)."""
        code = self.rows[name]
        succ, returns, calls, tails, _ = self.graphs[name]
        if self.leaves.get(name) or not code:
            return None
        arch, program = self.program.arch, self.program
        step = getattr(arch, "value_step", None)
        fixed = {r: (v & 0xFFFFFFFF, False) for r, v in program.image.fixed.items()}
        fn = program.by_name.get(name)
        start = next((i for i, row in enumerate(code) if fn is not None and row.address == fn.entry), 0)
        b = self.bit[reg]
        preds = [[] for _ in code]
        for i, out in enumerate(succ):
            for j in out:
                preds[j].append(i)
        call_at = dict(calls)
        found, seen, work = set(), set(), []

        def before(i):                      # the value in front of row i: behind each of its predecessors
            if i == start:
                return False                # (the caller's value reaches a way out)
            work.extend(preds[i])
            return True

        def constant(i):
            if step is None:
                return False
            insn = program.insn(code[i].address)
            try:
                values = step(insn, program.isa_at(insn.address), dict(fixed), program)[0]
            except Exception:
                return False
            v = values.get(reg)
            return v is not None and not v[1]
        for i in returns:
            work.append(i)
        for i, callee in tails:
            if self.may[callee] & b:
                found.add(callee)
            elif code[i].kind != "jump":
                work.append(i)              # (falls into the next function: its own write counts)
            elif not before(i):
                return None
        while work:
            i = work.pop()
            if i in seen:
                continue
            seen.add(i)
            row = code[i]
            if i in call_at:
                callee = call_at[i]
                if callee is None:
                    return None             # (a call through a pointer: its callee's value is not known)
                if self.may[callee] & b:
                    found.add(callee)
                    if not row.conditional:
                        continue
            elif row.writes & b:
                if row.stack == "pop" or not constant(i):
                    return None
                if not row.conditional:
                    continue
            if not before(i):
                return None
        return found

    def read_always(self, name, start, reg):
        """Whether every path from the instruction behind index `start` reads `reg` before
        something ends it or the function is left without passing it on (a return passes it on
        when the function's own callers read it)."""
        succ, returns, _, tails, _ = self.graphs[name]
        uses, kills, exits = self.final(name)
        b = self.bit[reg]
        returns, tail_at = set(returns), dict(tails)
        seen, stack = set(), list(succ[start])
        if not stack:
            return False
        while stack:
            j = stack.pop()
            if j in seen:
                continue
            seen.add(j)
            if uses[j] & b:
                continue
            if kills[j] & b:
                return False
            if j in returns or j in tail_at:
                if not exits[j] & b:
                    return False
                continue
            if not succ[j]:
                return False
            stack.extend(succ[j])
        return True

    def readers(self, name, address, reg):
        """Where `reg` is read after the call (or tail call) at `address` in function `name`:
        [("read", address)] for an instruction that reads it, ("return", address) for a return
        that passes it on to the function's own callers, ("tail", address, callee) for a tail call
        that passes it on (the callee reads it or lets it through to the callers)."""
        code = self.rows[name]
        succ, returns, calls, tails, _ = self.graphs[name]
        index = {row.address: i for i, row in enumerate(code)}
        b = self.bit[reg]
        uses, kills, _ = self.final(name)
        returns, tail_at = set(returns), dict(tails)
        start = index[address]
        if start in tail_at:
            return [("tail", address, tail_at[start])]
        out, seen, stack = [], set(), list(succ[start])
        while stack:
            j = stack.pop()
            if j in seen:
                continue
            seen.add(j)
            if uses[j] & b:
                out.append(("read", code[j].address))
                continue
            if kills[j] & b:
                continue
            if j in returns and self.kept[name] & b:
                out.append(("return", code[j].address))
            if j in tail_at and (self.reads[tail_at[j]] | self.formats.get(code[j].address, 0) |
                                 (self.kept[name] & ~self.may[tail_at[j]])) & b:
                out.append(("tail", code[j].address, tail_at[j]))
            stack.extend(succ[j])
        return sorted(out)


def analyse(program):
    """RegFlow of a Program (see the module's docstring): the program's own view of the first result
    register, the comparison's of the rest (Analysis.solve, result)."""
    analysis = Analysis(program).solve()
    program_view = (dict(analysis.kept), analysis.sites())
    return analysis.solve(compared=True).result(program_view)


# module name -> the bytes of its source as this process read them (_source); read when this module is
# imported for every module cache_key holds (_read_sources)
_SOURCES = {}


def _source(name):
    """The bytes of module NAME's source as this process read them first."""
    if name not in _SOURCES:
        import importlib.util
        module = sys.modules.get(name)
        path = module.__file__ if module is not None else importlib.util.find_spec(name).origin
        with open(path, "rb") as f:
            _SOURCES[name] = f.read()
    return _SOURCES[name]


def _read_sources():
    """Read the sources cache_key holds now, as this module is imported: a process keys its analysis by
    the code it runs, not by what the files hold when it stores the analysis. Read in cache_key, a suite
    that had imported the old code stored its analysis under the key of an edit made meanwhile, and the
    runs after it took the old analysis for the new code (2026-10-24). What a module imported later
    reads in the moment between is not seen (unknown whether it matters in practice)."""
    import importlib.util
    import pkgutil
    with open(__file__, "rb") as f:
        _SOURCES[__name__] = f.read()
    for name in ("dread.switches", "dread.program", "dread.arch", "dread.varargs", "dread.constants"):
        _source(name)
    for m in pkgutil.iter_modules(importlib.util.find_spec("dread.arch").submodule_search_locations):
        _source("dread.arch." + m.name)


def cache_key(target, image):
    h = hashlib.sha1()
    h.update(b"dread regflow %d\0" % VERSION)
    for module in (__name__, "dread.switches", type(image.arch).__module__, "dread.arch", "dread.program",
                   "dread.varargs", "dread.constants"):
        # the analysis, the arch layer (and the helpers of its bit rules), the decoder (it marks calls
        # through a register) and what finds the formats of calls (dread.varargs, by dread.constants), as
        # this process read them when it imported this module (_read_sources)
        h.update(_source(module))
    h.update(image.payload())
    for table in ("functions", "code_ranges"):
        path = image.table(table)
        if os.path.exists(path):
            with open(path, "rb") as f:
                h.update(f.read())
        h.update(b"\0")
    copies = [(c.kind, c.run, c.size, c.stored) for c in image.startup()]
    h.update(repr((target.abi.callee_saved, image.arch.describe(), image.base, copies)).encode())
    formats = sorted((getattr(target.abi, "formats", None) or {}).items())
    if formats:                                             # (the target's [abi] formats, where it has any)
        h.update(b"formats " + repr(formats).encode())
    syscall_reads = getattr(target.abi, "syscall_reads", None)
    if syscall_reads is not None:                           # (what it says a system call's handler reads)
        h.update(b"syscall_reads " + repr(list(syscall_reads)).encode())
    profile = getattr(getattr(target, "compiler", None), "profile", None)
    if profile and os.path.exists(profile):                 # the compiler's profile (dread cclab)
        with open(profile, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def load(target, image, program=None):
    """The RegFlow of an image, from the cache or analysed (and cached)."""
    path = os.path.join(target.build, "regflow", image.name + ".json")
    key = cache_key(target, image)
    try:
        with open(path) as f:
            data = json.load(f)
        if data.get("key") == key:
            return RegFlow.from_json(data["flow"])
    except (OSError, ValueError, KeyError):
        pass
    if program is None:
        from dread.program import Program
        program = Program(target, image)
    flow = analyse(program)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w") as f:
        json.dump({"key": key, "flow": flow.to_json()}, f, sort_keys=True)
    os.replace(tmp, path)
    return flow


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread regflow", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--all", action="store_true", help="every function's reads and kept")
    parser.add_argument("--why", action="store_true",
                        help="for the named functions: the instructions behind each kept register")
    parser.add_argument("--json", metavar="FILE", help="also write every function's facts (or the named ones') to "
                        "FILE as JSON (docs/formats.md)")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        flow = load(target, image)
    except tgt.TargetError as e:
        print("dread regflow: %s" % e, file=sys.stderr)
        return 1
    result = target.abi.result[0] if target.abi.result else None
    names = args.names or sorted(set(flow.kept) | set(flow.reads))
    if args.json:
        missing = [n for n in args.names if n not in flow.kept and n not in flow.reads]
        if missing:
            print("dread regflow: no function %s" % ", ".join(missing), file=sys.stderr)
            return 2
        write_json(args.json, json_record(flow, names, result))
    analysis = None
    if args.why:
        from dread.program import Program
        analysis = Analysis(Program(target, image)).solve(compared=True)
    for name in names:
        kept = flow.kept_regs(name)
        extra = [r for r in kept if r != result]
        if not (args.all or args.names or extra):
            continue
        print("%-28s reads %-16s kept %-12s overwritten %s" % (name, " ".join(flow.read_regs(name)) or "-",
                                                               " ".join(kept) or "-",
                                                               " ".join(flow.overwritten_regs(name)) or "-"))
        for reg in extra:
            sites = flow.sites_of(name, reg)
            if analysis is None:
                print("    %s read after the call by %s" % (reg, ", ".join("%s 0x%x" % s for s in sites[:6])))
                continue
            print("    %s:" % reg)
            for caller, address in sites:
                if caller not in analysis.rows:     # "the ABI" or "the compiler's profile" (abi_label)
                    print("        by %s: read after every call" % caller)
                    continue
                for what in analysis.readers(caller, address, reg):
                    if what[0] == "read":
                        text = "read at 0x%x" % what[1]
                    elif what[0] == "return":
                        text = "passed on by the return at 0x%x to the callers of %s" % (what[1], caller)
                    else:
                        text = "passed on by the tail call at 0x%x to %s" % (what[1], what[2])
                    print("        after %s 0x%x: %s" % (caller, address, text))
    if not args.names:
        print("dread regflow: %d functions keep a register besides %s for their callers"
              % (sum(1 for n in flow.kept if [r for r in flow.kept_regs(n) if r != result]), result))
    return 0


def json_record(flow, names, result):
    """dread regflow --json's record (docs/formats.md): the version of the analysis, the register names,
    the result register and each named function's facts (RegFlow.facts)."""
    leftover, computed = flow.leftover, flow.computed
    if leftover is not None:                # (as sets for the facts of many functions)
        flow.leftover = set(leftover)
    if computed is not None:
        flow.computed = set(computed)
    try:
        return {"version": VERSION, "names": [n for n in flow.names], "result": result,
                "functions": {name: flow.facts(name) for name in names}}
    finally:
        flow.leftover, flow.computed = leftover, computed


def write_json(path, data):
    """Write a record whole, renamed into place (a reader never sees half of it)."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, ".%s.tmp%d" % (os.path.basename(path), os.getpid()))
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


_read_sources()

if __name__ == "__main__":
    sys.exit(main())
