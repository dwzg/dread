"""dread.bound: a bound call in one statement of readable C.

dread.lift writes a call whose callee reads registers its caller leaves (a compiler's shared code, a
callee that reads r4-r11 as its caller left them), or behind which the caller reads what the callee
leaves beyond a C result, as a block that binds the registers to variables, makes the call in an asm
statement and takes them back (ARM: lift.Lifter._bound_call):

    {
        register u32 x0 __asm__("r0") = delay, x1 __asm__("r1") = r1, x2 __asm__("r2") = 5, x5 __asm__("r5") = arg;
        __asm__ volatile ("bl timer_wait" : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x5) : : "lr", "cc", "memory", "r12");
        delay = x0;
        r1 = x1;
        r3 = x5;
    }

which readable C printed over nine lines a call. compact writes it as one statement of dread.h's BOUND
(BOUND_R12 where r12 is among the registers, which the asm then binds instead of clobbering):

    BOUND(timer_wait, R0(delay), R1(r1), R2_IN(5), R5_IN_OUT(arg, r3));

Rn(v): v handed in rn and rn taken back into v; Rn_IN(e): e handed in rn, what the call leaves there
unread; Rn_IN_OUT(e, v): e handed in rn, rn taken back into v. The register is in the macro's name: as an
argument (IO(r0, v), until the same day) it was a name like any other, and the naming pilot's renaming of
a variable r0 renamed the register too, which no longer compiled. dread.h's macros make the same block (the
same instructions: tests/test_bound.py compiles both). Only the block as the lifter writes it is so
written: the asm a bl and nothing else (no flags read behind it, no stack arguments), its outputs the
registers' variables in their order, its clobbers lr, cc, memory and r12 unless r12 is bound (no FPU
registers), its inputs none, each variable taken back at most once into a plain variable (a cast there,
for a typed variable, keeps the block), and no variable of the block read after those. What follows the
write-backs in the block (`r12 = 0;`) follows the statement. Run on the finished tree (dread.readable,
after dread.polish): it changes how the call reads, nothing a table or a name is made for.
"""
import re

from dread import cparse as C

CLOBBERS = ['"lr"', '"cc"', '"memory"', '"r12"']
REGISTER = re.compile(r'^__asm__\("(r\d+)"\)$')


def compact(fn):
    """Every bound call of fn's readable C written as one BOUND statement (the module's docstring), in
    place; returns whether anything changed."""
    changed = []

    def fixed(s):
        """s, or the statements it becomes (a list) where it is a bound call's block."""
        if isinstance(s, C.Block):
            made = _statements(s)
            if made is not None:
                changed.append(s)
                return made
            s.body[:] = _fix_list(s.body)
            return s
        if isinstance(s, C.If):
            s.then = _one(fixed(s.then))
            if s.orelse is not None:
                s.orelse = _one(fixed(s.orelse))
        elif isinstance(s, (C.While, C.DoWhile, C.For, C.Switch)):
            s.body = _one(fixed(s.body))
        return s

    def _fix_list(stmts):
        out = []
        for s in stmts:
            got = fixed(s)
            out.extend(got if isinstance(got, list) else [got])
        return out

    fn.body.body[:] = _fix_list(fn.body.body)
    return bool(changed)


def _one(got):
    """A statement in an arm's or a loop's place: a list of several becomes a block."""
    if isinstance(got, list):
        return got[0] if len(got) == 1 else C.Block(got)
    return got


def _statements(blk):
    """The statements a bound call's block becomes (the BOUND statement, then what followed its
    write-backs), or None where blk is no such block as the lifter writes it."""
    body = [x for x in blk.body if not isinstance(x, (C.Note, C.Empty))]
    if len(body) < 2 or not isinstance(body[0], C.Decl) or body[0].specs.split() != ["register", "u32"]:
        return None
    regs = []                               # (its variable, its register, what it is handed)
    for d in body[0].items:
        m = REGISTER.match(d.asm or "")
        if not m or d.init is None or d.ptr or d.dims:
            return None
        regs.append((d.name, m.group(1), d.init))
    asm = body[1]
    if not isinstance(asm, C.Asm) or asm.inputs or asm.labels:
        return None
    m = re.match(r"^bl (\w+)$", "".join(p.strip('"') for p in asm.template.parts))
    if not m:
        return None
    outs = [("".join(o.constraint.parts), o.e.id if isinstance(o.e, C.Name) else None) for o in asm.outputs]
    if outs != [('"+r"', x) for x, _, _ in regs]:
        return None
    bound12 = any(r == "r12" for _, r, _ in regs)
    if ["".join(c.parts) for c in asm.clobbers] != (CLOBBERS[:3] if bound12 else CLOBBERS):
        return None
    names = {x for x, _, _ in regs}
    back, k = {}, 2
    while k < len(body):
        st = body[k]
        e = st.e if isinstance(st, C.ExprStmt) else None
        if not (isinstance(e, C.Assign) and e.op == "=" and isinstance(e.value, C.Name) and e.value.id in names
                and e.value.id not in back and isinstance(e.target, C.Name)):
            break
        back[e.value.id] = e.target.id
        k += 1
    rest = body[k:]
    if names & _names_in(rest) or any(_names_in([C.ExprStmt(init)]) & names for _, _, init in regs):
        return None
    args = []
    for x, r, init in regs:
        v = back.get(x)
        reg = r.upper()                     # (in the macro's name: renaming a variable r0 renames no register)
        if v is None:
            args.append(C.Call(C.Name(reg + "_IN"), [init]))
        elif isinstance(init, C.Name) and init.id == v:
            args.append(C.Call(C.Name(reg), [C.Name(v)]))
        else:
            args.append(C.Call(C.Name(reg + "_IN_OUT"), [init, C.Name(v)]))
    call = C.Call(C.Name("BOUND_R12" if bound12 else "BOUND"), [C.Name(m.group(1))] + args)
    stmt = C.ExprStmt(call)
    stmt.addr = getattr(blk, "addr", None) or getattr(asm, "addr", None) or getattr(body[0], "addr", None)
    return [stmt] + rest


def _names_in(stmts):
    """The names the statements' expressions name (and those statements hold)."""
    from dread import polish
    out = set()
    for st in polish._all_statements(stmts):
        for e in polish._stmt_exprs(st):
            out |= {x.id for x in polish._walk(e) if isinstance(x, C.Name)}
        if isinstance(st, C.Decl):
            for d in st.items:
                if d.init is not None:
                    out |= {x.id for x in polish._walk(d.init) if isinstance(x, C.Name)}
    return out
