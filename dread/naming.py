"""Names for readable C: the variables of a function that dread readable made, named by a proposal
(dread pilot --task readable: a language model's judgement) and applied by code.

A proposal is a map from the readable C's variable names (r0, r4, r2_1, s36) to names that say what
they hold, and a sentence on what the function does. It is kept in the image's readable_names table
(config/readable_names.json, docs/formats.md) only when it holds: every new name a C identifier that
is no keyword, no type or macro of dread.h, no callee, no other variable of the function, no two
alike; and the function's C with the names compiles to the same code as without them (check). With
it goes a digest of the C it was made for: when the transformations change that C later, the names
wait for a new proposal instead of landing on other variables (apply).

Memory at fixed addresses (*(volatile u32 *)0x20029aacu) is written as a name: a macro per address
and type above the function, which is the access itself, so the code cannot change. The name is the
table's (its globals, proposed for the whole image: dread pilot --task readable asks for them before
the functions' variables, from their uses in every readable function) or a default one that says
where it is: io_40004004 a device register ([memory] io), rom_c6e4 in a rom region, g_20029aac
anything else (apply_globals). A function's names are made for its C before the globals' names, so
naming a global later does not invalidate them. Memory that the readable_types plan puts into a
structure of static data (dread.statics) is written as a member of one object instead
(g_20000030.f_4, one macro and the structure's definition above the function), its names from the
same table (static_names: the structure's entry, else each field's address and type).

No tool can tell whether a name is right; the same code tells that it changed nothing else. How
close the names come to the source's: score (the bench's builds with debug information).

Functions' own names (dread pilot --task name) are proposals only, never applied: score_functions
compares them with true names (an ELF's symbols, or the functions table of a copy that has them).
"""
import hashlib
import json
import os
import re

from dread import cparse as C

TABLE = "readable_names"
KEYWORDS = {"auto", "break", "case", "char", "const", "continue", "default", "do", "double", "else", "enum",
            "extern", "float", "for", "goto", "if", "inline", "int", "long", "register", "restrict", "return",
            "short", "signed", "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned", "void",
            "volatile", "while", "_Bool", "_Complex", "_Imaginary", "asm", "__asm__", "__attribute__",
            "u8", "s8", "u16", "s16", "u32", "s32", "u64", "s64", "VAR", "VVAR", "REG8", "REG16", "REG32",
            "ALWAYS_INLINE", "ORIGINAL", "NULL", "frame", "sp0"}
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,47}$")


def digest(fn):
    """A digest of a function's readable C without names (the text a proposal was made for)."""
    return hashlib.sha1(C.unparse(fn, notes=False, style="readable").encode()).hexdigest()[:16]


def variables(fn):
    """The function's own variables: its parameters and what its body declares (nested ones too)."""
    out = [p[1] for p in fn.params]

    def visit(s):
        if isinstance(s, C.Decl):
            out.extend(d.name for d in s.items)
        for inner in _inner(s):
            visit(inner)
    for s in fn.body.body:
        visit(s)
    return out


def _inner(s):
    if isinstance(s, C.Block):
        return list(s.body)
    if isinstance(s, C.If):
        return [s.then] + ([s.orelse] if s.orelse is not None else [])
    if isinstance(s, (C.While, C.DoWhile, C.Switch)):
        return [s.body]
    if isinstance(s, C.For):
        return [s.body] + ([s.init] if isinstance(s.init, C.Decl) else [])
    return []


def other_names(tree, fn):
    """The names the new ones must not take: everything named in the file but the function's own
    variables (callees, labels, the file's declarations), and the keywords and dread.h's names."""
    from dread.cflow import walk_expr
    taken = set(KEYWORDS)
    for item in tree.items:
        if isinstance(item, C.Raw):
            taken |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", item.text))
        elif isinstance(item, C.Function):
            taken.add(item.name)
    own = set(variables(fn))

    def visit(s):
        if isinstance(s, (C.Label, C.Goto)):
            taken.add(s.name if isinstance(s, C.Label) else s.label)
        for e in _exprs(s):
            for x in walk_expr(e):
                if isinstance(x, C.Name) and x.id not in own:
                    taken.add(x.id)
                if isinstance(x, C.Member):
                    taken.add(x.name)
        for inner in _inner(s):
            visit(inner)
    for s in fn.body.body:
        visit(s)
    return taken - own


def _exprs(s):
    if isinstance(s, C.ExprStmt):
        return [s.e]
    if isinstance(s, C.Return):
        return [s.e] if s.e is not None else []
    if isinstance(s, (C.If, C.While, C.DoWhile)):
        return [s.cond]
    if isinstance(s, C.For):
        return [x for x in (s.init if not isinstance(s.init, C.Decl) else None, s.cond, s.step) if x is not None]
    if isinstance(s, C.Switch):
        return [s.e]
    if isinstance(s, C.Decl):
        return [d.init for d in s.items if d.init is not None] + [x for d in s.items for x in d.dims if x is not None]
    if isinstance(s, C.Asm):
        return [o.e for o in s.outputs + s.inputs]
    if isinstance(s, C.Case):
        return [s.value] if s.value is not None else []
    return []


def problems(tree, fn, names):
    """Why a map cannot be applied to the function (an empty list when it can)."""
    out = []
    own = set(variables(fn))
    taken = other_names(tree, fn)
    seen = {}
    for old, new in names.items():
        if old not in own:
            out.append("%s is no variable of %s" % (old, fn.name))
        elif not isinstance(new, str) or not IDENT.match(new):
            out.append("%r is no C name" % (new,))
        elif new in taken:
            out.append("%s is taken (a keyword, a type or macro of dread.h, a callee, a label)" % new)
        elif new in seen and seen[new] != old:
            out.append("%s given to both %s and %s" % (new, seen[new], old))
        else:
            seen[new] = old
    unchanged = own - set(names)
    for old, new in names.items():
        if new in unchanged and new != old:
            out.append("%s is another variable of %s" % (new, fn.name))
    return out


def rename(fn, names):
    """The function's variables renamed in place (parameters, declarations, every use), its head
    written anew with the parameters' names when it is plain (type name(parameters))."""
    from dread.cflow import walk_expr

    def visit(s):
        if isinstance(s, C.Decl):
            for d in s.items:
                if d.name in names:
                    d.name = names[d.name]
        for e in _exprs(s):
            for x in walk_expr(e):
                if isinstance(x, C.Name) and x.id in names:
                    x.id = names[x.id]
        if isinstance(s, C.For) and isinstance(s.init, C.Decl):
            visit(s.init)
        for inner in _inner(s):
            visit(inner)
    for s in fn.body.body:
        visit(s)
    m = re.match(r"^(\w+) (\w+)\((.*)\)$", fn.head.strip())
    new_params = [(t, names.get(n, n)) for t, n in fn.params]
    if m and new_params != fn.params:           # (a pointer's star beside its name: volatile u8 *buf)
        fn.head = "%s %s(%s)" % (m.group(1), m.group(2), ", ".join(
            "%s%s" % p if p[0].endswith("*") else "%s %s" % p for p in new_params) or "void")
        fn.params = new_params


def load(image, part="functions"):
    """{function: entry} of the image's readable_names table, or with part="globals" {key: entry} of
    its globals ({} when there is none)."""
    path = image.table(TABLE)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f).get(part, {})


def store(image, name, entry, part="functions"):
    """Put one function's entry (or one global's: part="globals") into the table (written sorted)."""
    path = image.table(TABLE)
    data = {"version": 1, "functions": {}}
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
    data.setdefault(part, {})[name] = entry
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(path + ".tmp", path)


def apply(tree, fn, entry, made_for=None):
    """Apply a table entry to the function when it was made for this C (its digest, or made_for: the
    digest taken before the globals got their names) and still holds; returns whether it was
    applied. The sentence goes above the function as a comment."""
    if not entry or entry.get("digest") != (made_for or digest(fn)) or problems(tree, fn, entry.get("names", {})):
        return False
    rename(fn, entry.get("names", {}))
    what = (entry.get("what") or "").strip()
    if what:
        k = tree.items.index(fn)
        text = "/* %s: %s (names: %s) */" % (fn.name, what.replace("*/", "* /"), entry.get("by", "proposed"))
        tree.items.insert(k, C.Raw(text))
    return True


ACCESS = re.compile(r"^volatile (\w+) \*$")


def fixed_access(e):
    """(address, type) when e is an access to memory at a fixed address (*(volatile u32 *)0x2000u)."""
    if (isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast) and isinstance(e.e.e, C.Num)
            and isinstance(e.e.e.value, int)):
        m = ACCESS.match(" ".join(e.e.type.split()))
        if m:
            return e.e.e.value, m.group(1)
    return None


def global_key(address, ctype):
    return "0x%x %s" % (address, ctype)


def default_name(address, ctype, memory=None):
    """io_40004004 (a device register: the target's [memory] io), rom_c6e4 (in a rom region),
    g_20029aac (anything else); an access other than a u32 one carries its type: g_20029aac_u8."""
    prefix = "g"
    if memory is not None:
        region = memory.region(address)
        if memory.is_io(address):
            prefix = "io"
        elif region is not None and region.kind == "rom":
            prefix = "rom"
    name = "%s_%x" % (prefix, address)
    return name if ctype == "u32" else "%s_%s" % (name, ctype)


def globals_in(fn):
    """{(address, type)} of the function's accesses to memory at fixed addresses."""
    from dread.cflow import walk_expr
    found = set()

    def visit(s):
        for e in _exprs(s):
            for x in walk_expr(e):
                a = fixed_access(x)
                if a is not None:
                    found.add(a)
        for inner in _inner(s):
            visit(inner)
    for s in fn.body.body:
        visit(s)
    return found


def _rewrite(e, f):
    """e with every subexpression x for which f(x) gives a replacement replaced (in place where it can)."""
    r = f(e)
    if r is not None:
        return r
    for slot in type(e).__slots__:
        v = getattr(e, slot, None)
        if isinstance(v, C.Expr):
            setattr(e, slot, _rewrite(v, f))
        elif isinstance(v, list) and any(isinstance(x, C.Expr) for x in v):
            setattr(e, slot, [_rewrite(x, f) if isinstance(x, C.Expr) else x for x in v])
    return e


def _rewrite_stmt(s, f, inner=True):
    """The statement's expressions rewritten by f (_rewrite), with inner those of the statements it
    holds too."""
    if isinstance(s, C.ExprStmt):
        s.e = _rewrite(s.e, f)
    elif isinstance(s, C.Return):
        if s.e is not None:
            s.e = _rewrite(s.e, f)
    elif isinstance(s, (C.If, C.While, C.DoWhile)):
        s.cond = _rewrite(s.cond, f)
    elif isinstance(s, C.For):
        if s.init is not None and not isinstance(s.init, C.Decl):
            s.init = _rewrite(s.init, f)
        if s.cond is not None:
            s.cond = _rewrite(s.cond, f)
        if s.step is not None:
            s.step = _rewrite(s.step, f)
    elif isinstance(s, C.Switch):
        s.e = _rewrite(s.e, f)
    elif isinstance(s, C.Decl):
        for d in s.items:
            if d.init is not None:
                d.init = _rewrite(d.init, f)
            d.dims = [_rewrite(x, f) if x is not None else None for x in d.dims]
    elif isinstance(s, C.Asm):
        for o in s.outputs + s.inputs:
            o.e = _rewrite(o.e, f)
    elif isinstance(s, C.Case):
        if s.value is not None:
            s.value = _rewrite(s.value, f)
    if inner:
        for x in _inner(s):
            _rewrite_stmt(x, f)


def static_key(address):
    """The globals part's key of a structure or an array of static data (dread.statics): its address."""
    return "0x%x struct" % address


def _static_rows(statics, arrays):
    """{address: (fields [(offset, type)], element size or None, count)} of the structures (element
    size None) and arrays of static data (an array's fields its elements')."""
    out = {b: (list(rows), None, 1) for b, rows in (statics or {}).items()}
    out.update({b: (list(rows), k, n) for b, (k, n, rows) in (arrays or {}).items()})
    return out


def scalar_array(k, rows):
    """The element type of an array whose elements are one field each (an array of scalars), else None."""
    from dread.statics import SIZES
    return rows[0][1] if len(rows) == 1 and rows[0][0] == 0 and SIZES[rows[0][1]] == k else None


def static_names(statics, named=None, memory=None, avoid=(), arrays=None):
    """{address: (object name, tag, {offset: field name})} for the structures of static data (statics:
    {address: [(offset, type)]}, dread.statics) and the arrays (arrays: {address: (element size,
    count, [(offset in the element, type)])}): the globals part's entry for the object (static_key:
    {"name", "fields": {"OFFSET": name}}) where it gives a C name, else default_name of its address as
    a word (g_20000030); its tag that name with _t (an array's: its elements' structure's; an array of
    scalars has none, its fields {}); a field's name its entry's, else the name the globals part gives
    the access at that address and type (a global named before the plan made it a field; an array's:
    in its first element), else f_<offset>. A name is used only where it is a C identifier, no
    keyword, unique in its structure, and no macro a file may hold beside it (an object or a global
    that stays a macro: it would expand inside a member or a tag); an object or a tag is none of avoid
    either (the names dread.shapes gives its structures: a tag defined twice, a macro inside one).
    Names depend on the table and the plan alone, so that an object reads alike in every function."""
    named = named or {}
    rows_of = _static_rows(statics, arrays)
    members = {global_key(b + j * (k or 0) + off, t) for b, (rows, k, n) in rows_of.items() for j in range(
        min(n, 4096) if k else 1) for off, t in rows}
    macros = {e.get("name") for k, e in named.items() if isinstance(e, dict) and k not in members
              and not k.endswith(" struct")}

    def good(name, taken=()):
        return isinstance(name, str) and bool(IDENT.match(name)) and name not in KEYWORDS and name not in taken
    objects, avoid = {}, set(avoid)
    for address in sorted(rows_of):
        name = (named.get(static_key(address)) or {}).get("name")
        if not good(name, macros | avoid | set(objects.values())):
            name = default_name(address, "u32", memory)
        objects[address] = name
    taken = macros | set(objects.values())
    out, tags = {}, set()
    for address, (rows, k, _) in sorted(rows_of.items()):
        name = objects[address]
        if k and scalar_array(k, rows):
            out[address] = (name, None, {})
            continue
        tag = name + "_t" if good(name + "_t", taken | tags | avoid) else "s_%x_t" % address
        tags.add(tag)
        given = (named.get(static_key(address)) or {}).get("fields") or {}
        fields, seen = {}, set()
        for off, ctype in rows:
            choice = given.get(str(off))
            if not good(choice, taken | seen):
                choice = (named.get(global_key(address + off, ctype)) or {}).get("name")
                if not good(choice, taken | seen):
                    choice = "f_%x" % off
            fields[off] = choice
            seen.add(choice)
        out[address] = (name, tag, fields)
    return out


def _plus(index, j):
    """index + j as readable C writes an element's index: index itself for 0, index - 1 for -1."""
    if j == 0:
        return index
    return C.Binary("+", index, C.Num("%d" % j)) if j > 0 else C.Binary("-", index, C.Num("%d" % -j))


BITS = ("&", "|", "^", "<<", ">>")       # operators whose result is a mask's or a shift's, never an address


NARROW = ("u8", "s8", "u16", "s16")      # casts that cut a value below a pointer's width


def _bit_value(e, bits=()):
    """Whether e (through casts) is the result of a mask, a shift (BITS, ~) or a narrowing cast, or a
    variable of bits (_bit_variables): a bit field's value, which no address is compared with."""
    while isinstance(e, C.Cast):
        if " ".join(e.type.split()) in NARROW:
            return True
        e = e.e
    if isinstance(e, C.Binary) and e.op in BITS or isinstance(e, C.Unary) and e.op == "~":
        return True
    return isinstance(e, C.Name) and e.id in bits


def _bit_variables(fn):
    """The function's variables that an assignment gives the result of a mask or a shift (_bit_value,
    or &=, |=, ^=, <<=, >>=): r3 = r0 & 0xf00u before if (r3 == 0x200u)."""
    from dread import statics as st
    from dread.cflow import walk_expr
    out = set()
    for s in st._statements(fn):
        for e in _exprs(s):
            for x in walk_expr(e):
                if isinstance(x, C.Assign) and isinstance(x.target, C.Name) and (
                        x.op[:-1] in BITS or (x.op == "=" and _bit_value(x.value))):
                    out.add(x.target.id)
    return out


def _word_read(e):
    """Whether e (through casts) is a word read from memory (an access of a u32 or s32) or a call's
    result: a value a pointer may be."""
    while isinstance(e, C.Cast):
        e = e.e
    if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast):
        m = ACCESS.match(" ".join(e.e.type.split()))
        return m is not None and m.group(1) in ("u32", "s32")
    return isinstance(e, C.Call)


def _loaded_variables(fn):
    """The function's variables (not its parameters, whose value at the entry is the caller's) that only
    plain assignments write, each of them a word read from memory or a call's result (_word_read) or
    another such variable: r5 = *(volatile u32 *)(r4 + 20) before if (r5 == 0x20029988u), a list's
    container compared with a list's address."""
    from dread import statics as st
    from dread.cflow import walk_expr
    defs, bad = {}, {n for _, n in fn.params}
    for s in st._statements(fn):
        if isinstance(s, C.Asm):
            bad.update(x.id for o in s.outputs for x in walk_expr(o.e) if isinstance(x, C.Name))
        if isinstance(s, C.Decl):
            for d in s.items:
                if d.init is not None:
                    defs.setdefault(d.name, []).append(d.init)
        for e in _exprs(s):
            for x in walk_expr(e):
                if isinstance(x, C.Assign) and isinstance(x.target, C.Name):
                    if x.op == "=":
                        defs.setdefault(x.target.id, []).append(x.value)
                    else:
                        bad.add(x.target.id)
                elif isinstance(x, (C.Post, C.Unary)) and x.op in ("++", "--") and isinstance(x.e, C.Name):
                    bad.add(x.e.id)
                elif isinstance(x, C.Unary) and x.op == "&" and isinstance(x.e, C.Name):
                    bad.add(x.e.id)         # (its address taken: a callee may write it)
    out, changed = set(), True
    while changed:
        changed = False
        for v, values in defs.items():
            if v in out or v in bad:
                continue
            if all(_word_read(x) or (isinstance(x, C.Name) and x.id in out) for x in values):
                out.add(v)
                changed = True
    return out


def _straight_values(fn):
    """{id(statement): {variable: whether its value is a word read (_word_read)}} of the values the
    function's variables hold at each statement by a plain assignment earlier in the same list of
    statements or in one around it, nothing between them writing the variable and no label or case
    between (dread.statics straight_facts' rule), taken before anything is rewritten: where a variable
    that holds several values in turn holds a word read from memory (clang -O2's vTaskResume compares
    r0_2, a list item's container just read, with a list's address, and r0_2 holds bytes later)."""
    from dread import statics as st
    out, cache = {}, {}

    def written(s):
        if id(s) not in cache:
            cache[id(s)] = st._written([s])
        return cache[id(s)]

    def walk(stmts, known):
        for s in stmts:
            names, labelled = written(s)
            if labelled:
                known = {}
            known = {v: x for v, x in known.items() if v not in names}
            out[id(s)] = known
            for inner in ([s.body] if isinstance(s, C.Block) else [[x] for x in _inner(s)]):
                walk(inner, known)
            got = st._assignment(s)
            if got is not None:
                known = dict(known)
                known[got[0]] = _word_read(got[1])
    walk(fn.body.body, {})
    return out


def _address_variables(fn, pointers=None, unknown=True):
    """The function's variables whose value it uses as an address: a variable the address of an access
    adds whole (*(volatile u32 *)(r3 + 4), *(volatile u8 *)(a0 + a4): the index too, which the sum does
    not tell from the pointer), an argument for a parameter its callee uses as a pointer (pointers:
    {callee: its parameters' indices that it uses as a pointer}, dread.shapes pointer_params; a callee
    it does not know, or pointers None, counts as using every one), with unknown also a value stored
    into memory, returned, or an asm's input (what becomes of it is not known; a variable itself, not a
    sum of several); and those whose value goes on into such a variable (w = v, w = v + 4, w = v + i, a
    conditional's arm) or that are compared with one (== and !=)."""
    from dread import statics as st
    from dread.cflow import walk_expr
    used, edges = set(), []

    def names_of(e):
        """The variables a value adds whole (statics.linear: through casts and sums), and a conditional's
        arms': what an address may be made from."""
        if isinstance(e, C.Cond):
            return names_of(e.t) + names_of(e.f)
        _, terms = st.linear(e)
        return [term.id for k, term in terms.values() if k == 1 and isinstance(term, C.Name)]

    def value_of(e):
        """The variable a value is (through casts and constants added), and a conditional's arms'."""
        if isinstance(e, C.Cond):
            return value_of(e.t) + value_of(e.f)
        names = names_of(e)
        _, terms = st.linear(e)
        return names if len(terms) == 1 else []
    for s in st._statements(fn):
        if unknown and isinstance(s, C.Return) and s.e is not None:
            used.update(value_of(s.e))
        if unknown and isinstance(s, C.Asm):
            for o in s.inputs:
                used.update(value_of(o.e))
        for e in _exprs(s):
            for x in walk_expr(e):
                if isinstance(x, C.Unary) and x.op == "*" and isinstance(x.e, C.Cast):
                    used.update(names_of(x.e.e))
                elif isinstance(x, C.Call):
                    callee = x.f.id if isinstance(x.f, C.Name) else None
                    for i, a in enumerate(x.args):
                        if pointers is None or callee not in pointers or i in pointers[callee]:
                            used.update(value_of(a))
                elif isinstance(x, C.Assign) and x.op == "=":
                    if isinstance(x.target, C.Name):
                        edges.extend((v, x.target.id) for v in names_of(x.value))
                    elif unknown:
                        used.update(value_of(x.value))
                elif isinstance(x, C.Binary) and x.op in ("==", "!="):
                    for a in value_of(x.l):
                        for b in value_of(x.r):
                            edges += [(a, b), (b, a)]
    changed = True
    while changed:
        changed = False
        for src, dst in edges:
            if dst in used and src not in used:
                used.add(src)
                changed = True
    return used


def apply_globals(tree, fn, named=None, memory=None, avoid=(), statics=None, names=None, arrays=None,
                  pointers=None, image=None):
    """The function's accesses to memory at fixed addresses written as names, a macro for each above
    the function (#define g_20029aac (*(volatile u32 *)0x20029aacu)): the table's name (named: the
    globals part) where it has one the file does not use for something else (nor avoid: the names the
    function's own variables will get), else default_name. With statics ({address: [(offset, type)]},
    the plan's structures of static data, dread.statics) an access that is a field of one, at its
    offset with its type, is written as a member of the structure's object (g_20000030.f_4): one macro
    for the structure, its definition above the function (as dread.shapes writes structures: every
    field naturally aligned, gaps of bytes), names from static_names (names: what it returned, made
    once for many functions; a structure's name that the function uses for something else gives way to
    its default name there, as a global's does).

    With arrays ({address: (element size, count, [(offset in the element, type)])}, the plan's arrays
    of static data) an access to an element's field is written as one: at a constant address inside the
    array (g_20000c74[1], g_2002990c[0].f_4) and at the array's constant plus an index times the
    element size (*(volatile u32 *)(0x200299f8u + 0x14u * r9 + 4) is g_200299f8[r9].f_4, the index
    plus the element the constant lies in where that is not the first: g_x[r3 + 1], or minus the
    elements it lies below the array's start where that is the address the code made, dread.statics
    base_of: 0x14u * (r0 - 1) + 0x20000020u + 4 is g_20000020[r0 - 1].f_4); the index may come
    through a variable that holds it times the element size (r8_3 = 0x14u * r8_2 ... 0x200299f8u +
    r8_3 is g_200299f8[r8_2]: dread.statics straight_facts says where that holds). The array's macro is
    a pointer to its first element, (#define g_200299f8 ((volatile struct g_200299f8_t *)0x200299f8u),
    of u32 for an array of scalars), so that C knows no bound for the index (an index past the end
    is undefined in C, and GCC derives loop bounds from that); the elements' structure is as large as
    an element. Where the code uses the address of an object or of its fields as a value (a call's
    argument, the value assigned or compared with == and !=, a result), a constant one inside an object
    or an array element's (the constant plus a scaled index) is written as that: (u32)&g_20029988,
    (u32)&g_20000030.f_8, (u32)&g_200299f8[r8_2]; an address inside an object at no field stays the
    number. A constant is written so only where it is used as an address (2026-10-23, after a review
    on a private firmware found a bit field's value compared with a masked word written as the address
    of an object that happened to lie at that number): an argument for a parameter the callee uses as
    a pointer (pointers: {callee: its parameters' indices}, dread.shapes pointer_params; a callee not in
    it, or pointers None, as before), a value assigned to a variable the function uses as an address
    (_address_variables) or stored into memory, a result, and an operand of == or != whose other
    operand is a variable used as an address or holding only words read from memory
    (_loaded_variables) or one there (_straight_values), a word read from memory or a call's result,
    never the result of a mask, a
    shift or a narrowing cast nor a variable given one (_bit_variables); and only for an object where
    the image keeps static data (image: [(start, end)], dread.statics image_spans; None: every object).
    An element's address the code computes (the constant plus a scaled index) is one wherever it is
    used, for an object in the image. Every access stays one volatile access of its width at its
    address; an access of another type than its field stays the macro it was (a read of the field's
    width and the other signedness is cast: (s16)g_200018de[r6]). Returns {key: name} of what it
    named: a structure's fields under
    their address and type as "NAME.FIELD", an array under static_key as its name and its elements'
    fields under the address and type of its first element's as "NAME[].FIELD" (what dread pilot asks
    names for)."""
    import bisect
    import copy
    from dread import statics as st
    from dread.constants import SMALLEST_POINTER
    named = named or {}
    found = globals_in(fn)
    rows_of = _static_rows(statics, arrays)
    if not found and not rows_of:
        return {}
    busy = other_names(tree, fn) | set(variables(fn)) | set(avoid)
    starts = sorted(rows_of)
    if rows_of and names is None:
        names = static_names(statics, named, memory, arrays=arrays)

    def inside(address):
        """(object address, element index, offset in the element or structure) of an address that lies
        inside an object, or None."""
        i = bisect.bisect_right(starts, address) - 1
        if i < 0:
            return None
        b = starts[i]
        rows, k, n = rows_of[b]
        if k:
            if address < b + k * n:
                return b, (address - b) // k, (address - b) % k
            return None
        end = max(off + st.SIZES[t] for off, t in rows)
        return (b, None, address - b) if address < b + end else None
    members, reads = {}, {}                 # (address, type) -> (object, element index or None, offset)
    for address, ctype in sorted(found):
        got = inside(address)
        field = dict(rows_of[got[0]][0]).get(got[2]) if got is not None else None
        if field == ctype:
            members[(address, ctype)] = got
        elif field is not None and st.SIZES[field] == st.SIZES.get(ctype, 4):
            reads[(address, ctype)] = got   # (the field's width, the other signedness: a read casts it)
    objects = {}                            # object -> (name, tag, fields) in this function

    def obj(b):
        if b not in objects:
            name, tag, fields = names[b]
            if name in busy:                # (a callee, a variable, a label of this function: the default here)
                name = default_name(b, "u32", memory)
            objects[b] = (name, tag, fields)
        return objects[b]
    for b, _, _ in members.values():
        obj(b)
    # (every object's names, also those this function reaches only by an address written later)
    taken_by_objects = {n for n, _, _ in (names or {}).values()} | {default_name(b, "u32", memory) for b in rows_of}
    chosen = {}
    for address, ctype in sorted(found):
        if (address, ctype) in members:
            continue
        name = (named.get(global_key(address, ctype)) or {}).get("name")
        if (not isinstance(name, str) or not IDENT.match(name) or name in busy or name in KEYWORDS
                or name in chosen.values() or name in taken_by_objects):
            name = default_name(address, ctype, memory)
        if name in taken_by_objects:        # (a word at an object's address it holds no such field for)
            name = "%s_%s" % (default_name(address, "u32", memory), ctype)
        chosen[(address, ctype)] = name
    out = {}

    def named_as(b, off, j):
        """Notes in out what an access to field off of object b (element j of an array) went into: a
        structure's field under its address and type ("NAME.FIELD"); an array under static_key, an
        element's field under the address and type of the first element's ("NAME[].FIELD")."""
        name, _, fields = obj(b)
        rows, k, _ = rows_of[b]
        if k is None:
            out[global_key(b + off, dict(rows)[off])] = "%s.%s" % (name, fields[off])
            return
        out[static_key(b)] = name
        if not scalar_array(k, rows):
            out[global_key(b + off, dict(rows)[off])] = "%s[].%s" % (name, fields[off])

    def element(b, j, off, index=None):
        """The member expression of field off of element j of object b (j None: a structure's field),
        index an expression added to j (an indexed access)."""
        name, tag, fields = obj(b)
        rows, k, _ = rows_of[b]
        e = C.Name(name)
        if k is not None:
            i = C.Num("%d" % j) if index is None else _plus(index, j)
            e = C.Index(e, i)
            if scalar_array(k, rows):
                return e
        return C.Member(e, fields[off], False)

    via = set()                             # (variables an index went through: r8_3 for g_200299f8[r8_2])

    def indexed(addr, facts):
        """(object, element index, offset, index expression) of an address that is an array's constant
        plus an index times its element size, or None. Where the constant lies below the address the
        code made (dread.statics base_of: table[i - 1].level, 0x14u * (r0 - 1) + 0x20000020u + 4) and an
        array starts there, the element is counted from that array's start (-1), not from the object the
        constant happens to lie in."""
        used = set()
        got = st.scaled(addr, facts, used)
        if got is None or got[2] is None:
            return None
        const, k, index = got
        base = st.base_of(addr, facts, memory)
        if base in rows_of and rows_of[base][1] == k and st.below_base(base, const, k, memory):
            where = (base, (const - base) // k, (const - base) % k)
        else:
            where = inside(const)
        if where is None or rows_of[where[0]][1] != k:
            return None
        via.update(used)
        return where[0], where[1], where[2], index

    bits = _bit_variables(fn) if rows_of else set()
    addressed = _address_variables(fn, pointers) if rows_of else set()
    loaded = _loaded_variables(fn) if rows_of else set()
    values = _straight_values(fn) if rows_of else {}
    here = [{}]                             # (what the variables hold at the statement being rewritten)

    def pointer_like(x):
        """Whether an operand a constant is compared with is one an address may be compared with: a
        variable the function uses as an address, that holds only words read from memory or holds one
        here (_straight_values), a word read from memory, a call's result (through casts); never the
        result of a mask or a shift, nor a variable given one."""
        if _bit_value(x, bits):
            return False
        while isinstance(x, C.Cast):
            x = x.e
        if isinstance(x, C.Name):
            return x.id in addressed or x.id in loaded or here[0].get(x.id, False)
        return _word_read(x)

    def as_address(use):
        """Whether a constant used so (("arg", callee, index), ("value", target), ("compare", the other
        operand), ("result",)) is used as an address."""
        kind = use[0]
        if kind == "arg":
            return pointers is None or use[1] not in pointers or use[2] in pointers[use[1]]
        if kind == "value":
            return not isinstance(use[1], C.Name) or use[1].id in addressed
        if kind == "compare":
            return pointer_like(use[1])
        return True

    def address_of(e, facts, f, use):
        """The address expression ((u32)&g_x.f_8, (u32)&g_x[r3]) of a value e that is an address
        inside an object at a field or an element, else None: a constant only where it is used as an
        address (use: as_address), and only for an object where the image keeps static data (image)."""
        if isinstance(e, C.Num) and isinstance(e.value, int):
            if e.value < SMALLEST_POINTER or not as_address(use):   # (a small number is a count)
                return None
            where = inside(e.value)
            index = None
        else:
            got = indexed(e, facts) if not isinstance(e, C.Num) else None
            if got is None:
                return None
            where, index = got[:3], _rewrite(copy.deepcopy(got[3]), f)
        if where is None or (image is not None and not st.holds(image, where[0])):
            return None
        b, j, off = where
        rows, k, _ = rows_of[b]
        if k is None and off == 0:          # (a structure's own address: &g_x, not its first field's)
            target = C.Name(obj(b)[0])
        elif k is not None and off == 0:    # (an element's: &g_x[3])
            name = obj(b)[0]
            i = C.Num("%d" % j) if index is None else _plus(index, j)
            target = C.Index(C.Name(name), i)
        elif off in dict(rows) and not (k is not None and scalar_array(k, rows)):
            target = element(b, j, off, index)
        else:
            return None
        if k is not None or off == 0:
            out[static_key(b)] = obj(b)[0]
        else:
            named_as(b, off, j)
        return C.Cast("u32", C.Unary("&", target))

    used = set()                            # (the macros the rewritten function still names)

    def make(facts, store=False):
        """The rewrite of an expression with facts (straight_facts) in front of it; store: what it
        rewrites is written (an assignment's target, an operand of ++ or --), so no access becomes a
        cast of a member (no lvalue in C)."""
        def f(e):
            a = fixed_access(e)
            if a is not None:
                if a in members or (a in reads and not store):
                    b, j, off = members.get(a) or reads[a]
                    named_as(b, off, j)
                    return element(b, j, off) if a in members else C.Cast(a[1], element(b, j, off))
                used.add(a)
                return C.Name(chosen[a])
            if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast) and arrays:
                m = ACCESS.match(" ".join(e.e.type.split()))
                got = indexed(e.e.e, facts) if m else None
                if got is not None:
                    b, j, off, index = got
                    field = dict(rows_of[b][0]).get(off)
                    if field == m.group(1) or (field is not None and not store
                                               and st.SIZES[field] == st.SIZES.get(m.group(1), 4)):
                        named_as(b, off, j)
                        got = element(b, j, off, _rewrite(copy.deepcopy(index), make(facts)))
                        return got if field == m.group(1) else C.Cast(m.group(1), got)
                return None
            if isinstance(e, C.Call):
                callee = e.f.id if isinstance(e.f, C.Name) else None
                return C.Call(_rewrite(e.f, f), [address_of(x, facts, f, ("arg", callee, i)) or _rewrite(x, f)
                                                 for i, x in enumerate(e.args)])
            if isinstance(e, C.Assign):
                value = (address_of(e.value, facts, f, ("value", e.target)) if e.op == "=" else None) \
                    or _rewrite(e.value, make(facts))
                return C.Assign(e.op, _rewrite(e.target, make(facts, True)), value)
            if isinstance(e, (C.Post, C.Unary)) and e.op in ("++", "--"):
                return type(e)(e.op, _rewrite(e.e, make(facts, True)))
            if isinstance(e, C.Binary) and e.op in ("==", "!="):
                return C.Binary(e.op, address_of(e.l, facts, f, ("compare", e.r)) or _rewrite(e.l, f),
                                address_of(e.r, facts, f, ("compare", e.l)) or _rewrite(e.r, f))
            return None
        return f

    if rows_of:
        def visit(s, facts):
            here[0] = values.get(id(s), {})
            f = make(facts)
            if isinstance(s, C.Return) and s.e is not None:
                s.e = address_of(s.e, facts, f, ("result",)) or _rewrite(s.e, f)
            else:
                _rewrite_stmt(s, f, inner=False)
        st.straight_facts(fn, visit, memory)
        st.drop_unread(fn, via)
    else:
        f0 = make({})
        for s in fn.body.body:
            _rewrite_stmt(s, f0)
    chosen = {a: n for a, n in chosen.items() if a in used}
    if not chosen and not objects:
        return out
    k_ = tree.items.index(fn)
    lines = ["#define %s (*(volatile %s *)0x%xu)" % (chosen[a], a[1], a[0]) for a in chosen]
    for b, (name, tag, _) in objects.items():
        rows, k, _ = rows_of[b]
        if k is None:
            lines.append("#define %s (*(volatile struct %s *)0x%xu)" % (name, tag, b))
        else:
            lines.append("#define %s ((volatile %s *)0x%xu)" % (name, scalar_array(k, rows) or "struct " + tag, b))
    lines = sorted(lines, key=lambda line: int(line.rsplit("0x", 1)[1].rstrip("u)"), 16))     # (by address)
    tree.items.insert(k_, C.Raw("\n".join(lines)))
    from dread.shapes import struct_text
    present = {item.text for item in tree.items if isinstance(item, C.Raw)}
    for b in sorted(objects, reverse=True):
        rows, k, _ = rows_of[b]
        if k is not None and scalar_array(k, rows):
            continue
        text = struct_text(objects[b][1], rows, objects[b][2], size=k)
        if text not in present:             # (a file whose other function has it already: C17 forbids a
            tree.items.insert(k_, C.Raw(text))      # second definition)
    out.update({global_key(*a): n for a, n in chosen.items()})
    return out


def same_code(target, image, before, after, work):
    """Whether two C files of one function compile to the same code (dread.build's lifted flags):
    the bytes and relocations of every code section."""
    from dread import build
    from dread.elf import Elf
    objs = []
    for label, text in (("a", before), ("b", after)):
        src = os.path.join(work, label)
        os.makedirs(src, exist_ok=True)
        for old in os.listdir(src):
            os.remove(os.path.join(src, old))
        with open(os.path.join(src, "f.c"), "w") as f:
            f.write(text)
        try:
            objs.append(build.compile_all(target, image, src, os.path.join(work, label + "-obj"), quiet=True,
                                          flags=target.compiler.generated_cflags())[0])
        except Exception:
            return False

    def code(obj):
        e = Elf(obj)
        out = {}
        for i, s in enumerate(e.sections):
            if s.flags & 4 and s.size:
                out[s.name] = (e.data(s), [(o, t, e.symbols[k].name, a) for o, t, k, a in e.relocations(i)])
        return out
    return code(objs[0]) == code(objs[1])


def truth(elf_path):
    """{entry address: {"name", "params": [names in order], "locals": {names}}} from an ELF's debug
    information (DWARF, read with pyelftools): what the source called a function's parameters and
    variables. {} when the ELF has none or pyelftools is missing."""
    try:
        from elftools.elf.elffile import ELFFile
    except ImportError:
        return {}
    out = {}
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        if not elf.has_dwarf_info():
            return {}
        dwarf = elf.get_dwarf_info()

        def name_of(die):
            attr = die.attributes.get("DW_AT_name")
            if attr is not None:
                return attr.value.decode(errors="replace")
            for ref in ("DW_AT_abstract_origin", "DW_AT_specification"):
                if ref in die.attributes:
                    try:
                        return name_of(die.get_DIE_from_attribute(ref))
                    except Exception:
                        return None
            return None

        def locals_of(die, into):
            for child in die.iter_children():
                if child.tag == "DW_TAG_variable":
                    n = name_of(child)
                    if n:
                        into.add(n)
                elif child.tag == "DW_TAG_lexical_block":
                    locals_of(child, into)
        for cu in dwarf.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_subprogram" or "DW_AT_low_pc" not in die.attributes:
                    continue
                entry = die.attributes["DW_AT_low_pc"].value & ~1
                params = [name_of(c) for c in die.iter_children() if c.tag == "DW_TAG_formal_parameter"]
                found = set()
                locals_of(die, found)
                out[entry] = {"name": name_of(die), "params": [p for p in params if p], "locals": found}
    return out


def words(name):
    """The words of a name, lower case, its type prefix and other short pieces dropped:
    pxNewListItem -> {new, list, item}; rx_buffer_len -> {buffer, len}."""
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name or "")
    return {p.lower() for p in parts if len(p) > 2}


def same_word(a, b):
    """Whether two words of names count as one: alike, or the shorter, of three letters or more, begins
    the longer (init and initialise, buf and buffer, task and tasks; also read and ready: a guess)."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return len(short) >= 3 and long_.startswith(short)


def similarity(a, b):
    """How alike two names are, from 0 to 1: their words (words()) paired one to one, alike words
    first, then words that begin one another (same_word), the pairs over the words of both (Jaccard):
    task_delay and vTaskDelay 1.0, list_insert_end and list_insert_tail 0.5, queue_send and
    queue_receive 0.33. Names alike but for case and separators are 1.0 (strcmp and str_cmp); names
    without such words (every piece shorter than three letters) 0 else. A measure of words, not of
    meaning: uart_putc and serial_write_char are 0, rx_byte and tx_byte 1."""
    if re.sub(r"[^a-z0-9]", "", (a or "").lower()) == re.sub(r"[^a-z0-9]", "", (b or "").lower()):
        return 1.0
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    pairs = wa & wb
    rest = sorted(wb - pairs)
    matched = len(pairs)
    for w in sorted(wa - pairs):
        for v in rest:
            if same_word(w, v):
                rest.remove(v)
                matched += 1
                break
    return matched / (len(wa) + len(wb) - matched)


# ---- words whatever the naming style: what a scorer compares (style_words, style_similarity)

# A camelCase name's lower-case head that is a type prefix of Hungarian notation, not a word:
# FreeRTOS's v, x, ux, px, pv, pc, ul, us, uc and prv (private), e for an enumeration, Windows' lp,
# sz, dw. Dropped only before a capital (vTaskDelay, prvIdleTask), never in snake_case.
HUNGARIAN = frozenset(("v", "x", "ux", "px", "pv", "pc", "prv", "ul", "us", "uc", "pul", "pus", "puc", "pux",
                       "ppx", "ppv", "e", "c", "s", "l", "b", "u", "p", "lp", "sz", "dw"))
STOPWORDS = frozenset(("the", "a", "an", "of", "to", "for", "from", "and", "or", "with", "by", "at", "into",
                       "is", "has"))
# Words that name one thing, the first the form a name's word becomes: synonyms that names use for one
# another and abbreviations with their whole word. Small on purpose: a class too wide makes wrong names
# alike (send and receive, get and set stay apart). A method's constant, not a firmware's.
SYNONYMS = (
    ("get", "read", "fetch", "load", "query", "retrieve", "obtain"),
    ("set", "write", "store", "assign", "save", "put"),
    ("create", "new", "alloc", "allocate", "malloc", "make", "construct", "spawn"),
    ("delete", "free", "remove", "destroy", "dealloc", "deallocate", "erase", "drop"),
    ("init", "initialise", "initialize", "initialisation", "initialization", "setup"),
    ("send", "transmit", "tx", "post", "push", "enqueue", "emit"),
    ("receive", "recv", "rcv", "rx", "pop", "dequeue"),
    ("start", "begin", "launch"),
    ("stop", "halt", "terminate"),
    ("check", "test", "verify", "validate"),
    ("find", "search", "lookup", "locate"),
    ("delay", "sleep"),
    ("count", "number", "num", "cnt"),
    ("size", "length", "len"),
    ("interrupt", "irq", "isr"),
    ("handler", "callback", "hook", "cb"),
    ("message", "msg"),
    ("buffer", "buf"),
    ("string", "str", "text"),
    ("task", "thread"),
    ("error", "err", "fault", "fail", "failure"),
    ("reset", "clear"),
    ("add", "insert", "append"),
    ("update", "refresh"),
    ("enable", "activate"),
    ("disable", "deactivate"),
    ("increment", "inc", "increase"),
    ("decrement", "dec", "decrease"),
    ("resume", "wake", "wakeup"),
    ("suspend", "pause"),
    ("lock", "acquire", "take"),
    ("unlock", "release", "give"),
    ("notify", "notification", "notif", "signal"),
    ("parameter", "param", "parm", "argument", "arg"),
    ("print", "printf", "display", "show"),
    ("item", "element", "node"),
    ("compare", "cmp"),
    ("copy", "cpy"),
    ("queue", "fifo"),
    ("timer", "tmr"),
    ("semaphore", "sem", "sema", "semphr"),
    ("mutex", "mtx"),
    ("priority", "prio", "pri"),
    ("current", "cur", "curr"),
    ("previous", "prev"),
    ("next", "nxt"),
    ("pointer", "ptr"),
    ("index", "idx"),
    ("address", "addr"),
    ("value", "val"),
    ("config", "cfg", "conf", "configuration", "configure"),
    ("context", "ctx"),
    ("memory", "mem"),
    ("command", "cmd"),
    ("source", "src"),
    ("destination", "dst", "dest"),
    ("function", "func", "fn"),
    ("handle", "hdl", "hndl"),
    ("header", "hdr"),
    ("information", "info"),
    ("manager", "mgr"),
    ("object", "obj"),
    ("position", "pos"),
    ("reference", "ref"),
    ("register", "reg"),
    ("request", "req"),
    ("return", "ret"),
    ("system", "sys"),
    ("event", "evt"),
    ("group", "grp"),
    ("stack", "stk"),
    ("state", "status", "sts"),
    ("control", "ctrl", "ctl"),
    ("scheduler", "sched", "schedule"),
    ("clock", "clk"),
    ("frequency", "freq"),
    ("watchdog", "wdt", "wdog"),
    ("uart", "usart", "serial"),
    ("device", "dev"),
    ("driver", "drv"),
    ("hardware", "hw"),
    ("available", "avail"),
    ("temporary", "tmp"),
    ("calculate", "calc"),
    ("utility", "util"),
    ("sequence", "seq"),
    ("statistics", "stats"),
)
# More words a run of lower-case letters may be cut into (strcmp: str cmp; systick: sys tick), besides
# those of SYNONYMS; a run is cut only into words of three letters or more, all of them known.
COMMON_WORDS = frozenset((
    "char", "byte", "word", "data", "list", "tick", "time", "flag", "bit", "mask", "mode", "flash", "port",
    "pin", "led", "key", "line", "file", "name", "type", "event", "idle", "yield", "switch", "swap", "fill",
    "zero", "move", "dump", "pad", "pack", "unpack", "parse", "format", "scan", "sum", "crc", "hash", "sort",
    "min", "max", "abs", "div", "mod", "mul", "sub", "neg", "shift", "align", "round", "trim", "split",
    "join", "cat", "chr", "dup", "tok", "case", "lower", "upper", "digit", "space", "out", "open", "close",
    "seek", "tell", "flush", "sync", "loop", "main", "run", "exit", "abort", "assert", "panic", "trap",
    "vector", "table", "entry", "power", "boot", "heap", "pool", "spin", "tail", "head", "first", "last",
    "end", "all", "one", "two", "low", "high", "fast", "slow", "safe", "user", "app", "demo", "port",
    "core", "cpu", "bus", "dma", "adc", "spi", "gpio", "can", "usb", "net", "ack", "map", "page", "sector",
    "block", "frame", "packet", "stream", "pipe", "channel", "slot", "tag", "id", "rtc", "pwm", "led",
))


def stem(word):
    """A word's stem, so that the forms of one word become one: tasks task, delayed delay, notified
    notify, creating and creation creat, initialised initializ (-ise as -ize). A few rules, not a
    stemmer's whole; words of three letters or fewer stay."""
    w = word.lower()
    if len(w) <= 3:
        return w
    w = re.sub(r"is(e|ed|es|ing|ation|ations)$", r"iz\1", w)
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) > 3:
        w = w[:-1]
    if w.endswith("ied") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("ing") and len(w) >= 6:
        w = w[:-3]
    elif w.endswith("ed") and len(w) >= 5:
        w = w[:-2]
    else:
        if w.endswith("tion") and len(w) > 6:
            w = w[:-3]
        elif w.endswith("e") and len(w) > 3:
            w = w[:-1]
        return w
    if len(w) > 2 and w[-1] == w[-2] and w[-1] not in "lsz" and w[-1] not in "aeiou":
        w = w[:-1]                          # (stopped stop, running run; filled fill)
    return w


CANONICAL = {}
for _class in SYNONYMS:
    for _w in _class:
        CANONICAL.setdefault(_w, _class[0])
CANONICAL_STEMS = {}
for _class in SYNONYMS:
    for _w in _class:
        CANONICAL_STEMS.setdefault(stem(_w), _class[0])
VOCABULARY = frozenset(CANONICAL) | COMMON_WORDS
VOCABULARY_STEMS = frozenset(stem(w) for w in VOCABULARY)


def canonical_word(word):
    """The form a word of a name is compared in: its class's first word (SYNONYMS), found as it is or
    by its stem; else its stem."""
    w = word.lower()
    if w in CANONICAL:
        return CANONICAL[w]
    s = stem(w)
    return CANONICAL_STEMS.get(s, s)


def cut_run(run):
    """A run of lower-case letters cut into known words (VOCABULARY, three letters or more each), the
    fewest pieces, when it is no known word itself and the cut covers all of it: strcmp [str, cmp],
    systick [sys, tick]; else [run]."""
    if len(run) < 6 or not run.isalpha() or run in VOCABULARY or stem(run) in VOCABULARY_STEMS:
        return [run]
    best = {0: []}
    for end in range(3, len(run) + 1):
        for start in range(0, end - 2):
            if start in best and run[start:end] in VOCABULARY:
                cut = best[start] + [run[start:end]]
                if end not in best or len(cut) < len(best[end]):
                    best[end] = cut
    found = best.get(len(run))
    return found if found and len(found) > 1 else [run]


def style_words(name):
    """The words of a name as a scorer compares them, whatever its naming style: split at case changes,
    underscores and digits, a compiler's clone suffix (.constprop.0) dropped; a camelCase name's
    Hungarian head dropped (HUNGARIAN: vTaskDelay,
    pxCurrentTCB, prvIdleTask); a run of letters cut into known words (cut_run: strcmp, systick); each
    word in its class's form (canonical_word: abbreviations and synonyms, word forms); stop words and
    single letters dropped, digits kept (timer0 against timer1). A list, in the name's order."""
    name = (name or "").split(".")[0] or name or ""     # (a compiler's clone: f.constprop.0, f.part.1)
    pieces_ = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)
    head = re.match(r"[_\d]*([a-z]+)[A-Z]", name)
    if head and head.group(1) in HUNGARIAN and pieces_ and pieces_[0] == head.group(1):
        pieces_ = pieces_[1:]
    out = []
    for p in pieces_:
        for part in cut_run(p.lower()):
            if part in STOPWORDS or (len(part) < 2 and not part.isdigit()):
                continue
            out.append(canonical_word(part))
    return out


def style_similarity(a, b):
    """How alike two names are whatever their style, from 0 to 1: their style_words() paired one to
    one, alike first, then words that begin one another (same_word), over the words of both (Jaccard),
    as similarity() pairs words(): vTaskDelay and task_sleep 1.0, uxListRemove and list_delete_item
    0.67, xQueueReceive and queue_send 0.33. Names alike but for case and separators are 1.0; names
    without words 0. Still a measure of words: it knows the synonyms SYNONYMS lists, not meaning."""
    if re.sub(r"[^a-z0-9]", "", (a or "").lower()) == re.sub(r"[^a-z0-9]", "", (b or "").lower()) and (a or b):
        return 1.0
    wa, wb = set(style_words(a)), set(style_words(b))
    if not wa or not wb:
        return 0.0
    pairs = wa & wb
    rest = sorted(wb - pairs)
    matched = len(pairs)
    for w in sorted(wa - pairs):
        for v in rest:
            if same_word(w, v):
                rest.remove(v)
                matched += 1
                break
    return matched / (len(wa) + len(wb) - matched)


AGREE = 0.5         # two names agree when their similarity() is at least this (a vote's groups)


def vote(samples, keep=None, agree=AGREE):
    """The name a vote over samples settles on (self-consistency): (name, agreement, index), or None
    when no sample has a name. samples are proposed names in the order asked, None for a question
    that got none; keep, a name proposed before (by an earlier pass), runs as a candidate too.
    A candidate's agreement is the number of samples whose similarity() to it is at least agree
    (a sample agrees with itself); the candidate most samples agree with wins, a tie going to keep,
    then to the larger sum of similarities, then to the name proposed most often (case aside), then
    to the earlier sample. index is the winning sample's (None for keep). agreement over the number
    of samples asked is the vote's confidence."""
    names = [s for s in samples if s]
    if not names:
        return None
    candidates = ([(keep, None)] if keep else []) + [(s, i) for i, s in enumerate(samples) if s]
    best = None
    for name, index in candidates:
        sims = [similarity(name, s) for s in names]
        rank = (sum(1 for x in sims if x >= agree), index is None, round(sum(sims), 9),
                sum(1 for s in names if s.lower() == name.lower()), -(index or 0))
        if best is None or rank > best[0]:
            best = (rank, name, index)
    return best[1], best[0][0], best[2]


def function_truth(path):
    """{entry address: name} of true function names: an ELF's function symbols, or a functions table
    (TSV with a header, address and name its first two columns, as config/functions.tsv); unnamed
    ones (FUN_1a2b) left out."""
    from dread.samecode import UNNAMED
    out = {}
    with open(path, "rb") as f:
        elf = f.read(4) == b"\x7fELF"
    if elf:
        from dread.elf import Elf, STT_FUNC
        for s in Elf(path).symbols:
            if s.kind == STT_FUNC and s.name and s.shndx:
                out[s.value & ~1] = s.name
    else:
        for line in open(path).read().splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].strip():
                try:
                    out[int(parts[0], 0)] = parts[1].strip()
                except ValueError:
                    continue
    return {a: n for a, n in out.items() if n and not UNNAMED.match(n)}


def pieces(name):
    """A name's pieces, lower case, short ones too: ui_drawKeys -> [ui, draw, keys]."""
    return [p.lower() for p in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name or "")]


def score_functions(proposed, true):
    """Proposed function names ({address: name}) against true ones ({address: name}): (rows, totals)
    over the addresses both have. A row: address, true, proposed, exact (case aside), same words (in
    any order), overlap (shared words over the words of both, words as words(): pieces of three
    letters or more), same prefix (both names of two pieces or more, the first alike: ui_, fs_), and
    style (style_similarity: the words whatever the naming style, abbreviations and synonyms one).
    Of all of them style tells best a name a judge reading the source calls right from one it calls
    wrong (FreeRTOS, 2026-10-22: docs/decisions.md); the others stay for comparing with earlier runs."""
    rows = []
    for a in sorted(set(proposed) & set(true)):
        t, p = true[a], proposed[a]
        tw, pw = words(t), words(p)
        tp, pp = pieces(t), pieces(p)
        rows.append({"address": a, "true": t, "proposed": p, "exact": t.lower() == p.lower(),
                     "same_words": bool(tw) and tw == pw,
                     "overlap": len(tw & pw) / len(tw | pw) if tw | pw else 0.0,
                     "prefix": None if len(tp) < 2 or len(pp) < 2 else tp[0] == pp[0],
                     "style": style_similarity(t, p)})
    n = len(rows)
    prefixed = [r for r in rows if r["prefix"] is not None]
    totals = {"functions": n, "exact": sum(r["exact"] for r in rows),
              "same_words": sum(r["same_words"] for r in rows),
              "shared_word": sum(r["overlap"] > 0 for r in rows),
              "half_shared": sum(r["overlap"] >= 0.5 for r in rows),
              "overlap": sum(r["overlap"] for r in rows) / n if n else 0.0,
              "prefixed": len(prefixed), "same_prefix": sum(bool(r["prefix"]) for r in prefixed),
              "style": sum(r["style"] for r in rows) / n if n else 0.0,
              "style_half": sum(r["style"] >= 0.5 for r in rows), "style_same": sum(r["style"] == 1.0 for r in rows)}
    return rows, totals


def data_symbols(elf_path):
    """[(address, size, name)] of the ELF's data objects (its symbol table), sorted."""
    try:
        from elftools.elf.elffile import ELFFile
        from elftools.elf.sections import SymbolTableSection
    except ImportError:
        return []
    out = []
    with open(elf_path, "rb") as f:
        for section in ELFFile(f).iter_sections():
            if isinstance(section, SymbolTableSection):
                for sym in section.iter_symbols():
                    if sym["st_info"]["type"] == "STT_OBJECT" and sym.name:
                        out.append((sym["st_value"], sym["st_size"], sym.name))
    return sorted(set(out))


def score_globals(image, elf_path):
    """The table's globals against the ELF's data symbols: ([rows], totals); a row says whether the
    name is the symbol's at that address (exact, ignoring case), shares a word with it, or names a
    place inside a symbol (a member of a structure, an element of an array: inside)."""
    table = load(image, "globals")
    symbols = data_symbols(elf_path)
    rows = []
    totals = {"globals": 0, "exact": 0, "words": 0, "inside": 0, "unknown": 0}
    for key, entry in sorted(table.items()):
        address = int(key.split()[0], 16)
        sym = next(((a, s, n) for a, s, n in symbols if a == address), None) or next(
            ((a, s, n) for a, s, n in symbols if a < address < a + s), None)
        name = entry.get("name", "")
        row = {"key": key, "name": name, "symbol": sym[2] if sym else None,
               "offset": address - sym[0] if sym else None}
        totals["globals"] += 1
        if sym is None:
            totals["unknown"] += 1
        elif address != sym[0]:
            totals["inside"] += 1
            row["words"] = bool(words(name) & words(sym[2]))
            totals["words"] += row["words"]
        else:
            row["exact"] = name.lower() == sym[2].lower()
            row["words"] = bool(words(name) & words(sym[2]))
            totals["exact"] += row["exact"]
            totals["words"] += row["words"]
        rows.append(row)
    return rows, totals


def score(program, image, elf_path):
    """How close the table's names come to the source's: per function with an entry, the parameters
    compared by position (exact, ignoring case, and by shared words) and the local names found among
    the source's variables; with the totals. ([rows], totals)."""
    table = load(image)
    known = truth(elf_path)
    rows = []
    totals = {"functions": 0, "params": 0, "exact": 0, "words": 0, "locals": 0, "local_hits": 0}
    for name, entry in sorted(table.items()):
        fn = program.by_name.get(name)
        src = known.get(fn.entry) if fn is not None else None
        if src is None:
            continue
        names = entry.get("names", {})
        regs = list(program.target.abi.arg_regs)
        proposed = [names.get(r) for r in regs[:len(src["params"])]]
        exact = shared = 0
        pairs = []
        for mine, theirs in zip(proposed, src["params"]):
            if mine is None:
                continue
            pairs.append((mine, theirs))
            exact += mine.lower() == theirs.lower()
            shared += bool(words(mine) & words(theirs))
        local_names = [v for k, v in names.items() if k not in regs]
        hits = sum(1 for v in local_names if v.lower() in {x.lower() for x in src["locals"]})
        rows.append({"function": name, "pairs": pairs, "exact": exact, "words": shared,
                     "locals": len(local_names), "local_hits": hits})
        totals["functions"] += 1
        totals["params"] += len(pairs)
        totals["exact"] += exact
        totals["words"] += shared
        totals["locals"] += len(local_names)
        totals["local_hits"] += hits
    return rows, totals


DEFAULT_STRUCT = re.compile(r"^(s_\d+|f_[0-9a-f]+|pad_[0-9a-f]+)$")


def struct_names(plan, entries, taken=()):
    """{s_N: (name, {offset: field name})} of the plan's structures (dread.shapes) that the table's
    structs part names. An entry carries anchors, the function:variable pairs that pointed to its
    structure when it was named; it goes to the structure that shares most of them now (the numbers
    s_N change whenever the plan is made anew), its field names to the offsets the structure still
    has. A name that is no C identifier, a keyword, one of taken (the globals' names: macros, which
    would expand inside a tag or a member) or a default name (s_3, f_2c) is left out; a name two
    entries give goes to the structure with more anchors."""
    pointing = {}
    for f, variables_ in plan.typed.items():
        for v, k in variables_.items():
            pointing.setdefault(k, set()).add("%s:%s" % (f, v))
    choices = []
    for key, entry in entries.items():
        anchors = set(entry.get("anchors", [key]))
        for k, ps in pointing.items():
            n = len(anchors & ps)
            if n:
                choices.append((n, key, k))
    bad = set(KEYWORDS) | set(taken)

    def good(name):
        return isinstance(name, str) and IDENT.match(name) and name not in bad and not DEFAULT_STRUCT.match(name)
    out, used_entries, used_names = {}, set(), set()
    for n, key, k in sorted(choices, key=lambda c: (-c[0], c[1], c[2])):
        if k in out or key in used_entries:
            continue
        entry = entries[key]
        name = entry.get("name")
        if not good(name) or name in used_names:
            continue
        offsets = {off for off, _ in plan.members(k)}      # (a structure held inside it is a member too)
        fields, seen = {}, set()
        for off, fname in sorted((entry.get("fields") or {}).items(), key=lambda kv: int(kv[0])):
            off = int(off)
            if off in offsets and good(fname) and fname not in seen:
                fields[off] = fname
                seen.add(fname)
        out[k] = (name, fields)
        used_entries.add(key)
        used_names.add(name)
    return out


def struct_anchors(plan, k):
    """The function:variable pairs pointing to structure k of the plan, sorted."""
    return sorted("%s:%s" % (f, v) for f, variables_ in plan.typed.items() for v, kk in variables_.items() if kk == k)


def score_structs(program, plan, names, elf_path):
    """The structures' and fields' names against the source's (debug information): for each typed
    parameter whose source type is a pointer to a structure, our structure's name against the names
    of that type (its typedefs and its tag, as compared below), and each named field against the
    member names on the way to the source's leaf at its offset (xStateListItem, pxNext). Each pair of
    our structure and the source's counted once. Names compare without case and a trailing _t:
    exactly, or by a shared word (words). ([rows], totals)."""
    from dread import shapes
    src = shapes.source_params(elf_path)
    regs = list(program.target.abi.arg_regs)
    rows, seen = [], set()
    totals = {"structs": 0, "exact": 0, "words": 0, "fields": 0, "field_exact": 0, "field_words": 0}

    def norm(n):
        return re.sub(r"_t$", "", n.lower())
    for f, variables_ in sorted(plan.typed.items()):
        fn = program.by_name.get(f)
        members = src.get(fn.entry, {}) if fn is not None else {}
        for v, k in sorted(variables_.items()):
            if v not in regs or k not in names or regs.index(v) not in members:
                continue
            tnames, leaves = members[regs.index(v)]
            if (k, tnames[-1]) in seen:
                continue
            seen.add((k, tnames[-1]))
            mine, fields = names[k]
            by_off = {off: path for (off, _size), path in leaves.items() if path}
            row = {"struct": k, "name": mine, "source": tnames, "exact": any(norm(mine) == norm(t) for t in tnames),
                   "words": any(words(mine) & words(t) for t in tnames), "fields": []}
            for off, fname in sorted(fields.items()):
                path = by_off.get(off)
                if path:
                    row["fields"].append((fname, ".".join(path), fname.lower() == path[-1].lower(),
                                          any(words(fname) & words(p) for p in path)))
            totals["structs"] += 1
            totals["exact"] += row["exact"]
            totals["words"] += row["words"]
            totals["fields"] += len(row["fields"])
            totals["field_exact"] += sum(1 for x in row["fields"] if x[2])
            totals["field_words"] += sum(1 for x in row["fields"] if x[3])
            rows.append(row)
    return rows, totals
