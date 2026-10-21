"""Names for readable C: the variables of a function that dread readable made, named by a proposal
(dread pilot --task readable: a language model's judgement) and applied by code.

A proposal is a map from the readable C's variable names (r0, r4, r2_1, s36) to names that say what
they hold, and a sentence on what the function does. It is kept in the image's readable_names table
(config/readable_names.json, docs/formats.md) only when it holds: every new name a C identifier that
is no keyword, no type or macro of dread.h, no callee, no other variable of the function, no two
alike; and the function's C with the names compiles to the same code as without them (check). With
it goes a digest of the C it was made for: when the transformations change that C later, the names
wait for a new proposal instead of landing on other variables (apply).

No tool can tell whether a name is right; the same code tells that it changed nothing else. How
close the names come to the source's: score (the bench's builds with debug information).
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
    if m and new_params != fn.params:
        fn.head = "%s %s(%s)" % (m.group(1), m.group(2), ", ".join("%s %s" % p for p in new_params) or "void")
        fn.params = new_params


def load(image):
    """{function: entry} of the image's readable_names table ({} when there is none)."""
    path = image.table(TABLE)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f).get("functions", {})


def store(image, name, entry):
    """Put one function's entry into the table (written sorted, one entry per function)."""
    path = image.table(TABLE)
    data = {"version": 1, "functions": {}}
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
    data.setdefault("functions", {})[name] = entry
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(path + ".tmp", path)


def apply(tree, fn, entry):
    """Apply a table entry to the function when it was made for this C (its digest) and still holds;
    returns whether it was applied. The sentence goes above the function as a comment."""
    if not entry or entry.get("digest") != digest(fn) or problems(tree, fn, entry.get("names", {})):
        return False
    rename(fn, entry.get("names", {}))
    what = (entry.get("what") or "").strip()
    if what:
        k = tree.items.index(fn)
        text = "/* %s: %s (names: %s) */" % (fn.name, what.replace("*/", "* /"), entry.get("by", "proposed"))
        tree.items.insert(k, C.Raw(text))
    return True


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
                                          flags=target.compiler.lifted_cflags)[0])
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
