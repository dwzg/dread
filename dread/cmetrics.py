"""Figures of C functions: how large and how deeply nested readable C is, against the source it came from.

The bench's readable step (dread.bench) measures each function's readable C (dread.readable) on the
tree of dread.cparse, and the source function it was compiled from on clang's syntax tree (clang
-Xclang -ast-dump=json on the preprocessed source). Both go through one skeleton (statements,
control statements and their arms, gotos, labels) and are counted by one function (count), so that a
statement or a level of nesting means the same on both sides:

- a statement: an expression statement, a return, break, continue or goto, an asm statement, each
  initialised variable of a declaration (`int x = f();` is `x = f();`), and each control statement
  (if, while, do, for, switch) once; not blocks, labels, case labels, empty statements, declarations
  without an initialiser, nor static locals (initialised at load time). A GNU statement expression's
  statements count where it stands.
- depth: the deepest nesting of control statements, 0 for straight-line code; an else that holds one
  if alone (else if) is no level deeper, and `do { ... } while (0)` (a macro's statement wrapper) is a
  block.
- gotos and labels; loops (while, do, for: a loop the compiler unrolled is none in the readable C).

Readable C alone also gets what a reader trips over and what helps (readable_figures): casts (not
counting those of raw accesses), raw memory accesses through pointer casts (`*(volatile u32 *)(r4 +
56)`), of them raw frame accesses (the address built from the frame's base sp0), accesses as
structure members (`p->f_38`, and `p->f_4.f_8` once, a member of a structure held inside another;
the frame's object, `stack36.f_4`, apart; a member's address counts as well), memory at fixed addresses
written as a name (dread.naming's macros: `io_e000ed04`), and the variables (parameters too), typed
ones (a structure or a pointer to one) apart, as are those declared a pointer to anything else (pointers:
`volatile u8 *p`, dread.vartypes) and those declared signed (signed: s8, s16, s32); a member of a
structure of static data (`g_20000030.f_4`, dread.statics) or an element of an array of it
(`g_20000c74[1]`, `g_200299f8[r3].f_4`: one each) counts as one apart (fixed_members), not as a name
of memory at a fixed address; of those, the elements reached with an index that is no constant
(fixed_indexed); and the addresses of such objects, their fields and elements written as such
(`&g_20029988`, `&g_x.f_8`, `&g_200299f8[r3]`: fixed_addresses).

The source is taken after preprocessing: what the compiler compiled, macros expanded (FreeRTOS's
configASSERT is an if and a call, as in the readable C). A function the compiler inlined into the
measured one is counted in the readable C but not in the source function's figures; out_of_line lists
the inlined functions (DWARF's inlined subroutines) so that a report can say so. Clones (GCC's
.constprop, .isra, .part) are matched to their source function by DWARF's abstract origin, by entry
address, never by name; a .part is a piece of its source function. What is not established: that the
readable C does what the source does (the gate's cases establish that it does what the original does).
"""
import json
import os
import re
import subprocess

from dread.elf import dwarf_code_value
from dread import cparse

FIGURES = ("statements", "depth", "gotos", "labels", "loops")


# ---- the skeleton: ("s",) a statement, ("g",) a goto, ("l", items) a label, ("block", items),
# ("if", then, else or None), ("loop", body), ("switch", body)

def count(items):
    """{statements, depth, gotos, labels, loops} of a skeleton list (see the module's docstring)."""
    out = dict.fromkeys(FIGURES, 0)

    def flat(items):                        # (blocks spread out: for telling an else if)
        got = []
        for it in items:
            got.extend(flat(it[1]) if it[0] == "block" else [it])
        return got

    def walk(items, depth):
        for it in items:
            kind = it[0]
            if kind == "s":
                out["statements"] += 1
            elif kind == "g":
                out["statements"] += 1
                out["gotos"] += 1
            elif kind == "l":
                out["labels"] += 1
                walk(it[1], depth)
            elif kind == "block":
                walk(it[1], depth)
            elif kind == "if":
                out["statements"] += 1
                out["depth"] = max(out["depth"], depth + 1)
                walk(it[1], depth + 1)
                if it[2] is not None:
                    inner = flat(it[2])
                    if len(inner) == 1 and inner[0][0] == "if":
                        walk(inner, depth)          # else if: the same level
                    else:
                        walk(it[2], depth + 1)
            else:                                   # loop, switch
                out["statements"] += 1
                out["loops"] += kind == "loop"
                out["depth"] = max(out["depth"], depth + 1)
                walk(it[1], depth + 1)
    walk(items, 0)
    return out


# ---- readable C (dread.cparse)

def _arm(s):
    return s.body if isinstance(s, cparse.Block) else [s] if s is not None else []


def _zero(e):
    return isinstance(e, cparse.Num) and e.value == 0


def skeleton(stmts):
    """The skeleton of dread.cparse statements."""
    out = []
    for s in stmts:
        if isinstance(s, cparse.Block):
            out.append(("block", skeleton(s.body)))
        elif isinstance(s, cparse.If):
            out.append(("if", skeleton(_arm(s.then)), skeleton(_arm(s.orelse)) if s.orelse is not None else None))
        elif isinstance(s, cparse.DoWhile) and _zero(s.cond):
            out.append(("block", skeleton(_arm(s.body))))
        elif isinstance(s, (cparse.While, cparse.DoWhile, cparse.For)):
            out.append(("loop", skeleton(_arm(s.body))))
        elif isinstance(s, cparse.Switch):
            out.append(("switch", skeleton(_arm(s.body))))
        elif isinstance(s, cparse.Goto):
            out.append(("g",))
        elif isinstance(s, cparse.Label):
            out.append(("l", []))
        elif isinstance(s, cparse.Decl):
            if not re.search(r"\b(static|extern|typedef)\b", s.specs or ""):
                out.extend(("s",) for d in s.items if d.init is not None)
        elif isinstance(s, (cparse.ExprStmt, cparse.Return, cparse.Break, cparse.Continue, cparse.Asm)):
            out.append(("s",))
        # Case, Empty, Note: nothing
    return out


def nodes(x):
    """Every node of a dread.cparse tree under x (x included), statements and expressions."""
    stack = [x]
    while stack:
        n = stack.pop()
        if isinstance(n, list):
            stack.extend(reversed(n))
            continue
        if not isinstance(n, cparse.Node):
            continue
        yield n
        for k in reversed(n.__slots__):
            if k not in ("addr", "line", "end_line"):
                v = getattr(n, k, None)
                if isinstance(v, (cparse.Node, list)):
                    stack.append(v)


FIXED = re.compile(r"#define\s+(\w+)\s+\(\*\(volatile\b")          # dread.naming's fixed-address names
FIXED_STRUCT = re.compile(r"#define\s+(\w+)\s+\(\*\(volatile struct\s+\w+\s*\*\)")    # (of those, structures of
#                                                     static data; not a word holding a structure's pointer)
FIXED_ARRAY = re.compile(r"#define\s+(\w+)\s+\(\(volatile\b")          # (arrays of static data: a pointer)
STRUCT_TYPEDEF = re.compile(r"typedef\s+struct\b.*\}\s*(?:__attribute__\s*\(\(.*?\)\)\s*)?(\w+)\s*;\s*$", re.S)


def _has_frame_base(e):
    return any(isinstance(n, cparse.Name) and n.id == "sp0" for n in nodes(e))


READABLE = FIGURES + ("casts", "raw", "frame_raw", "members", "frame_members", "fixed", "fixed_members", "variables",
                      "typed", "fixed_indexed", "fixed_addresses", "pointers", "signed")


def readable_figures(tree, fn):
    """The figures of one function of a dread.cparse file (count's, and casts, raw, frame_raw,
    members, frame_members, fixed, fixed_members, variables, typed, fixed_indexed, fixed_addresses,
    pointers, signed: see the module's docstring)."""
    out = count(skeleton(fn.body.body))
    fixed, statics, structs, arrays = set(), set(), set(), set()
    for item in tree.items if tree is not None else []:
        if isinstance(item, cparse.Raw):
            for line in item.text.splitlines():
                m = FIXED.match(line.strip())
                if m:
                    fixed.add(m.group(1))
                m = FIXED_STRUCT.match(line.strip())
                if m:
                    statics.add(m.group(1))
                m = FIXED_ARRAY.match(line.strip())
                if m:
                    arrays.add(m.group(1))
            m = STRUCT_TYPEDEF.match(item.text.strip())
            if m:
                structs.add(m.group(1))
    raw_casts, inner = set(), set()
    extra = dict.fromkeys(("casts", "raw", "frame_raw", "members", "frame_members", "fixed", "fixed_members",
                           "fixed_indexed", "fixed_addresses"), 0)

    def static(x):
        """The innermost base of a chain of members and elements (g_x[3].f_4: g_x) and whether an index on
        the way is no constant, its nodes marked as counted."""
        indexed = False
        while isinstance(x, (cparse.Member, cparse.Index)):
            if isinstance(x, cparse.Index) and not isinstance(x.index, cparse.Num):
                indexed = True
            inner.add(id(x))
            x = x.base
        return x, indexed
    for n in nodes(fn.body):
        if isinstance(n, cparse.Unary) and n.op == "*" and isinstance(n.e, cparse.Cast) and "*" in n.e.type:
            extra["raw"] += 1
            raw_casts.add(id(n.e))
            if _has_frame_base(n.e.e):
                extra["frame_raw"] += 1
        elif isinstance(n, cparse.Unary) and n.op == "&" and id(n.e) not in inner \
                and isinstance(n.e, (cparse.Name, cparse.Member, cparse.Index)):
            x = n.e
            while isinstance(x, (cparse.Member, cparse.Index)):
                x = x.base
            if isinstance(x, cparse.Name) and x.id in statics | arrays:
                static(n.e)
                inner.add(id(n.e))
                inner.add(id(x))
                extra["fixed_addresses"] += 1
        elif isinstance(n, cparse.Member) and id(n) not in inner:
            # (a member of a structure held inside one, p->f_4.f_8, is one member: of a structure behind
            # a pointer when the chain has an arrow, of a structure of static data when its object is one)
            arrow, x = n.arrow, n.base
            while isinstance(x, cparse.Member):
                inner.add(id(x))
                arrow, x = arrow or x.arrow, x.base
            if not arrow and isinstance(x, cparse.Index) and isinstance(static(x)[0], cparse.Name) \
                    and static(x)[0].id in arrays:
                base, indexed = static(x)   # (an element's field of an array of static data: g_x[3].f_4)
                inner.add(id(base))
                extra["fixed_members"] += 1
                extra["fixed_indexed"] += indexed
            elif not arrow and isinstance(x, cparse.Name) and x.id in statics:
                inner.add(id(x))
                extra["fixed_members"] += 1
            else:
                extra["members" if arrow else "frame_members"] += 1
        elif isinstance(n, cparse.Index) and id(n) not in inner and isinstance(n.base, cparse.Name) \
                and n.base.id in arrays:
            inner.add(id(n.base))           # (an element of an array of scalars of static data: g_x[1])
            extra["fixed_members"] += 1
            extra["fixed_indexed"] += not isinstance(n.index, cparse.Num)
        elif isinstance(n, cparse.Name) and n.id in fixed and id(n) not in inner:
            extra["fixed"] += 1
    extra["casts"] = sum(1 for n in nodes(fn.body) if isinstance(n, cparse.Cast) and id(n) not in raw_casts)
    out.update(extra)

    def typed(spec, ptr=""):
        words = set(re.findall(r"\w+", spec or ""))
        return "struct" in words or bool(words & structs)
    variables = [(t, n, "") for t, n in fn.params]
    for d in nodes(fn.body):
        if isinstance(d, cparse.Decl) and not re.search(r"\b(typedef|extern)\b", d.specs or ""):
            variables.extend((d.specs, item.name, item.ptr or "") for item in d.items)
    out["variables"] = len(variables)
    out["typed"] = sum(1 for spec, _, _ in variables if typed(spec))
    out["pointers"] = sum(1 for spec, _, ptr in variables if ("*" in spec or ptr) and not typed(spec))
    out["signed"] = sum(1 for spec, _, ptr in variables if not ("*" in spec or ptr)
                        and re.sub(r"\b(volatile|const|register)\s+", "", spec or "").strip() in ("s8", "s16", "s32"))
    return out


# ---- the source (clang's JSON syntax tree)

SIMPLE = {"ReturnStmt", "BreakStmt", "ContinueStmt", "GCCAsmStmt", "MSAsmStmt"}


def _statement_expressions(node):
    """The skeletons of the GNU statement expressions ({ ... }) inside an expression."""
    out, stack = [], list(node.get("inner") or [])
    while stack:
        n = stack.pop()
        if not n:
            continue
        if n.get("kind") == "StmtExpr":
            for c in n.get("inner") or []:
                out.extend(clang_skeleton(c))
        else:
            stack.extend(n.get("inner") or [])
    return out


def _is_zero(node):
    while node and node.get("kind") in ("ParenExpr", "ImplicitCastExpr", "CStyleCastExpr"):
        node = (node.get("inner") or [None])[0]
    return bool(node) and node.get("kind") == "IntegerLiteral" and str(node.get("value")) == "0"


def clang_skeleton(node):
    """The skeleton of one statement of clang's JSON syntax tree (a list: a declaration may be several)."""
    if not node:
        return []
    kind = node.get("kind")
    inner = node.get("inner") or []
    if kind == "CompoundStmt":
        return [("block", [it for c in inner for it in clang_skeleton(c)])]
    if kind == "IfStmt":
        has_else = node.get("hasElse")
        then = inner[-2] if has_else else inner[-1]
        cond = inner[-3] if has_else else inner[-2]
        return _statement_expressions({"inner": [cond]}) + \
            [("if", clang_skeleton(then), clang_skeleton(inner[-1]) if has_else else None)]
    if kind == "DoStmt":
        body, cond = inner[0], inner[-1]
        if _is_zero(cond):
            return [("block", clang_skeleton(body))]
        return [("loop", clang_skeleton(body))]
    if kind in ("WhileStmt", "ForStmt"):
        return [("loop", clang_skeleton(inner[-1]))]
    if kind == "SwitchStmt":
        return [("switch", clang_skeleton(inner[-1]))]
    if kind in ("CaseStmt", "DefaultStmt"):
        return [("block", clang_skeleton(inner[-1]))] if inner else []
    if kind == "LabelStmt":
        return [("l", clang_skeleton(inner[-1]) if inner else [])]
    if kind == "AttributedStmt":
        stmts = [c for c in inner if c and not c.get("kind", "").endswith("Attr")]
        return clang_skeleton(stmts[-1]) if stmts else []
    if kind in ("GotoStmt", "IndirectGotoStmt"):
        return [("g",)]
    if kind == "NullStmt":
        return []
    if kind == "DeclStmt":
        out = []
        for v in inner:
            if v.get("kind") == "VarDecl" and v.get("init") and v.get("storageClass") not in ("static", "extern"):
                out.append(("s",))
                out.extend(_statement_expressions(v))
        return out
    if kind in SIMPLE:
        return [("s",)] + _statement_expressions(node)
    return [("s",)] + _statement_expressions(node)      # an expression statement


def source_functions(ast):
    """{name: count()} of the function definitions in a translation unit (clang's JSON syntax tree,
    its top level). A name is defined once in a translation unit (static or not)."""
    out = {}
    for decl in ast.get("inner") or []:
        if decl.get("kind") != "FunctionDecl" or not decl.get("name"):
            continue
        body = [c for c in decl.get("inner") or [] if c.get("kind") == "CompoundStmt"]
        if body:
            out[decl["name"]] = count(clang_skeleton(body[-1]))
    return out


def clang_ast(path, flags, clang="clang"):
    """(clang's JSON syntax tree of the preprocessed C file at path, [errors]): flags give the target
    (--target=..., -m...) and the standard; the tree comes with errors too (clang recovers), which are
    returned for the report. Raises OSError when clang is missing."""
    r = subprocess.run([clang] + list(flags) + ["-x", "cpp-output", "-fsyntax-only", "-Wno-everything",
                                                "-Xclang", "-ast-dump=json", path], capture_output=True, text=True)
    errors = [line for line in r.stderr.splitlines() if ": error:" in line]
    try:
        return json.loads(r.stdout) if r.stdout else {}, errors
    except ValueError as e:
        return {}, errors + ["the tree is not JSON: %s" % e]


# ---- DWARF: which source function each out-of-line function is

def out_of_line(elf_path):
    """{entry address: {"name", "cu", "inlined": [names]}} of the subprograms with code in an ELF's
    debug information (pyelftools): the source function's name (through DW_AT_abstract_origin and
    DW_AT_specification: a clone's), the path of the translation unit that holds it (DW_AT_name,
    joined to DW_AT_comp_dir), and the functions inlined into it (nested inlined subroutines
    too, in order, each instance once). {} without debug information; ImportError without pyelftools."""
    from elftools.elf.elffile import ELFFile
    out = {}
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        if not elf.has_dwarf_info():
            return {}
        dwarf = elf.get_dwarf_info()

        def name_of(die, depth=0):
            attr = die.attributes.get("DW_AT_name")
            if attr is not None:
                return attr.value.decode(errors="replace")
            for ref in ("DW_AT_abstract_origin", "DW_AT_specification"):
                if ref in die.attributes and depth < 8:
                    try:
                        return name_of(die.get_DIE_from_attribute(ref), depth + 1)
                    except Exception:
                        return None
            return None

        def inlined(die, into):
            for child in die.iter_children():
                if child.tag == "DW_TAG_inlined_subroutine":
                    into.append(name_of(child) or "?")
                if child.tag in ("DW_TAG_inlined_subroutine", "DW_TAG_lexical_block"):
                    inlined(child, into)
        for cu in dwarf.iter_CUs():
            top = cu.get_top_DIE()
            cu_name = top.attributes.get("DW_AT_name")
            comp_dir = top.attributes.get("DW_AT_comp_dir")
            path = cu_name.value.decode(errors="replace") if cu_name is not None else None
            if path and comp_dir is not None and not os.path.isabs(path):
                path = os.path.join(comp_dir.value.decode(errors="replace"), path)
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_subprogram" or "DW_AT_low_pc" not in die.attributes:
                    continue
                entry = dwarf_code_value(elf, die.attributes["DW_AT_low_pc"].value)
                names = []
                inlined(die, names)
                out[entry] = {"name": name_of(die), "cu": os.path.normpath(path) if path else None,
                              "inlined": names}
    return out


# ---- summaries

def median(values):
    v = sorted(values)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def quartiles(values):
    """(first quartile, median, third quartile) of values (the medians of the lower and upper halves),
    or None for none."""
    v = sorted(values)
    if not v:
        return None
    n = len(v)
    lower, upper = v[:n // 2], v[(n + 1) // 2:]
    return (median(lower) if lower else v[0], median(v), median(upper) if upper else v[-1])
