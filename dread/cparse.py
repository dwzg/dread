"""The C that DREAD's lifters write, as a tree and back.

dread lift writes one C file per function (dread.lift, dread.lift_riscv, dread.lift_msp430): plain
C with GNU extended asm and attributes, one line per instruction, each ending in a comment with the
instruction's address and text. parse() reads such a file into a tree whose statements keep those
addresses; unparse() prints a tree as C. The dialect is narrow, and what the parser does not know is
an error (ParseError): the function then stays as the lifter wrote it. dread.readable transforms the
tree.

The tree: a File holds top-level items, Raw (comments, preprocessor lines, declarations, kept as
written) and Function (a definition, its body a Block). Statements: Block, Decl, ExprStmt, If,
Goto, Label, Return, While, DoWhile, For, Switch, Case, Break, Continue, Asm, Empty, Note (an
instruction that left no statement: its comment alone). Each statement has addr (the instruction's
address and text from its line's comment, or None), line and end_line. Expressions: Name, Num,
Str, Unary, Post, Cast, Binary, Cond, Assign, Call, Index, Member, Comma, Sizeof, InitList.
"""
import ast
import re


class ParseError(Exception):
    pass


# --- tokens ---

TOKEN = re.compile(r"""
    (?P<ws>[ \t\r\f\v]+)
  | (?P<nl>\n)
  | (?P<comment>/\*.*?\*/)
  | (?P<linecomment>//[^\n]*)
  | (?P<pp>\#[^\n]*)
  | (?P<num>0[xX][0-9a-fA-F]+[uUlL]*|[0-9]+\.[0-9]*(?:[eE][-+]?[0-9]+)?[fFlL]?|[0-9]+[uUlL]*)
  | (?P<id>[A-Za-z_][A-Za-z_0-9]*)
  | (?P<str>"(?:[^"\\\n]|\\.)*")
  | (?P<chr>'(?:[^'\\\n]|\\.)+')
  | (?P<op>\.\.\.|<<=|>>=|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^]=|[-+*/%&|^!~<>=?:;,.()\[\]{}])
""", re.X | re.S)


class Tok:
    __slots__ = ("kind", "text", "line", "start", "end")

    def __init__(self, kind, text, line, start, end):
        self.kind, self.text, self.line, self.start, self.end = kind, text, line, start, end

    def __repr__(self):
        return "Tok(%s %r @%d)" % (self.kind, self.text, self.line)


def tokenize(text):
    out, pos, line = [], 0, 1
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if m is None:
            raise ParseError("line %d: cannot read %r" % (line, text[pos:pos + 20]))
        kind = m.lastgroup
        if kind == "nl":
            line += 1
        elif kind != "ws":
            if kind == "pp" and text[:m.start()].rstrip(" \t").rpartition("\n")[2] != "":
                raise ParseError("line %d: '#' inside a line" % line)
            out.append(Tok("comment" if kind == "linecomment" else kind, m.group(), line, m.start(), m.end()))
            line += m.group().count("\n")
        pos = m.end()
    return out


# --- the tree ---

class Node:
    __slots__ = ()

    def __repr__(self):
        return "%s(%s)" % (type(self).__name__, ", ".join("%s=%r" % (k, getattr(self, k, None))
                                                        for k in self.__slots__ if k not in ("line", "end_line")))

    def __eq__(self, other):                # (the same tree: where it stood and how it was written aside)
        return type(self) is type(other) and all(getattr(self, k, None) == getattr(other, k, None)
                                                 for k in self.__slots__ if k not in ("line", "end_line", "raw"))

    def __hash__(self):
        return id(self)


class Expr(Node):
    __slots__ = ()


class Name(Expr):
    __slots__ = ("id",)

    def __init__(self, id):
        self.id = id


class Num(Expr):
    __slots__ = ("text", "value")

    def __init__(self, text, value=None):
        self.text = text
        if value is None:
            value = num_value(text)
        self.value = value


def num_value(text):
    t = text.rstrip("uUlLfF") if not text.lower().startswith("0x") else text.rstrip("uUlL")
    try:
        return int(t, 0) if not re.fullmatch(r"0[0-7]+", t) else int(t, 8)
    except ValueError:
        return float(t)


class Str(Expr):
    __slots__ = ("parts",)                  # the literals as written (adjacent ones joined by C)

    def __init__(self, parts):
        self.parts = list(parts)


class Unary(Expr):
    __slots__ = ("op", "e")                 # - + ~ ! * & ++ -- (prefix)

    def __init__(self, op, e):
        self.op, self.e = op, e


class Post(Expr):
    __slots__ = ("op", "e")                 # ++ -- (postfix)

    def __init__(self, op, e):
        self.op, self.e = op, e


class Cast(Expr):
    __slots__ = ("type", "e")               # type: the type name as written, spaces normalised

    def __init__(self, type, e):
        self.type, self.e = type, e


class Binary(Expr):
    __slots__ = ("op", "l", "r")

    def __init__(self, op, l, r):
        self.op, self.l, self.r = op, l, r


class Cond(Expr):
    __slots__ = ("c", "t", "f")

    def __init__(self, c, t, f):
        self.c, self.t, self.f = c, t, f


class Assign(Expr):
    __slots__ = ("op", "target", "value")   # = += -= ...

    def __init__(self, op, target, value):
        self.op, self.target, self.value = op, target, value


class Call(Expr):
    __slots__ = ("f", "args")

    def __init__(self, f, args):
        self.f, self.args = f, list(args)


class Index(Expr):
    __slots__ = ("base", "index")

    def __init__(self, base, index):
        self.base, self.index = base, index


class Member(Expr):
    __slots__ = ("base", "name", "arrow")

    def __init__(self, base, name, arrow):
        self.base, self.name, self.arrow = base, name, arrow


class Comma(Expr):
    __slots__ = ("items",)

    def __init__(self, items):
        self.items = list(items)


class Sizeof(Expr):
    __slots__ = ("type", "e")               # one of them

    def __init__(self, type=None, e=None):
        self.type, self.e = type, e


class InitList(Expr):
    __slots__ = ("items",)

    def __init__(self, items):
        self.items = list(items)


class Designated(Expr):
    __slots__ = ("designator", "value")     # in an InitList: .field = value, [index] = value

    def __init__(self, designator, value):
        self.designator, self.value = designator, value


class Compound(Expr):
    __slots__ = ("type", "init")            # a compound literal: (type){ ... }

    def __init__(self, type, init):
        self.type, self.init = type, init


class Stmt(Node):
    __slots__ = ()


def _stmt_slots(*names):
    return names + ("addr", "line", "end_line")


class Block(Stmt):
    __slots__ = _stmt_slots("body")

    def __init__(self, body, addr=None):
        self.body, self.addr = list(body), addr


class Declarator(Node):
    __slots__ = ("ptr", "name", "dims", "attrs", "init", "asm")    # asm: a register variable's __asm__("r0")

    def __init__(self, name, ptr="", dims=(), attrs="", init=None, asm=""):
        self.name, self.ptr, self.dims, self.attrs, self.init, self.asm = name, ptr, list(dims), attrs, init, asm


class Decl(Stmt):
    __slots__ = _stmt_slots("specs", "items")   # specs: the type words as written

    def __init__(self, specs, items, addr=None):
        self.specs, self.items, self.addr = specs, list(items), addr


class ExprStmt(Stmt):
    __slots__ = _stmt_slots("e")

    def __init__(self, e, addr=None):
        self.e, self.addr = e, addr


class If(Stmt):
    __slots__ = _stmt_slots("cond", "then", "orelse")

    def __init__(self, cond, then, orelse=None, addr=None):
        self.cond, self.then, self.orelse, self.addr = cond, then, orelse, addr


class Goto(Stmt):
    __slots__ = _stmt_slots("label")

    def __init__(self, label, addr=None):
        self.label, self.addr = label, addr


class Label(Stmt):
    __slots__ = _stmt_slots("name")

    def __init__(self, name, addr=None):
        self.name, self.addr = name, addr


class Return(Stmt):
    __slots__ = _stmt_slots("e")

    def __init__(self, e=None, addr=None):
        self.e, self.addr = e, addr


class While(Stmt):
    __slots__ = _stmt_slots("cond", "body")

    def __init__(self, cond, body, addr=None):
        self.cond, self.body, self.addr = cond, body, addr


class DoWhile(Stmt):
    __slots__ = _stmt_slots("body", "cond")

    def __init__(self, body, cond, addr=None):
        self.body, self.cond, self.addr = body, cond, addr


class For(Stmt):
    __slots__ = _stmt_slots("init", "cond", "step", "body")     # init: an Expr, a Decl or None

    def __init__(self, init, cond, step, body, addr=None):
        self.init, self.cond, self.step, self.body, self.addr = init, cond, step, body, addr


class Switch(Stmt):
    __slots__ = _stmt_slots("e", "body")

    def __init__(self, e, body, addr=None):
        self.e, self.body, self.addr = e, body, addr


class Case(Stmt):
    __slots__ = _stmt_slots("value")       # None: default

    def __init__(self, value, addr=None):
        self.value, self.addr = value, addr


class Break(Stmt):
    __slots__ = _stmt_slots()

    def __init__(self, addr=None):
        self.addr = addr


class Continue(Stmt):
    __slots__ = _stmt_slots()

    def __init__(self, addr=None):
        self.addr = addr


class Operand(Node):
    __slots__ = ("name", "constraint", "e")     # [name] "constraint" (e)

    def __init__(self, constraint, e, name=None):
        self.constraint, self.e, self.name = constraint, e, name


class Asm(Stmt):
    __slots__ = _stmt_slots("quals", "template", "outputs", "inputs", "clobbers", "labels")

    def __init__(self, quals, template, outputs=(), inputs=(), clobbers=(), labels=None, addr=None):
        self.quals, self.template = quals, template         # template: a Str
        self.outputs, self.inputs, self.clobbers = list(outputs), list(inputs), list(clobbers)
        self.labels, self.addr = labels, addr               # labels: asm goto's, or None


class Empty(Stmt):
    __slots__ = _stmt_slots()

    def __init__(self, addr=None):
        self.addr = addr


class Note(Stmt):
    __slots__ = _stmt_slots("text")         # a comment of its own line: (address, text) in addr, or text

    def __init__(self, text, addr=None):
        self.text, self.addr = text, addr


class Raw(Node):
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text


class Function(Node):
    __slots__ = ("head", "name", "params", "body", "raw")   # head: the declaration as written

    def __init__(self, head, name, params, body, raw=""):
        self.head, self.name, self.params, self.body, self.raw = head, name, list(params), body, raw


class File(Node):
    __slots__ = ("items",)

    def __init__(self, items):
        self.items = list(items)

    def function(self, name):
        for item in self.items:
            if isinstance(item, Function) and item.name == name:
                return item
        return None


# --- the parser ---

TYPE_WORDS = {"void", "char", "short", "int", "long", "float", "double", "signed", "unsigned", "_Bool",
              "const", "volatile", "static", "inline", "register", "extern", "auto", "typedef", "union",
              "struct", "enum", "__inline__", "__volatile__", "__restrict", "restrict",
              "u8", "s8", "u16", "s16", "u32", "s32", "u64", "s64"}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "<<=", ">>=", "&=", "^=", "|="}
BINARY = {"*": 13, "/": 13, "%": 13, "+": 12, "-": 12, "<<": 11, ">>": 11, "<": 10, "<=": 10, ">": 10,
          ">=": 10, "==": 9, "!=": 9, "&": 8, "^": 7, "|": 6, "&&": 5, "||": 4}
ADDRESS = re.compile(r"\s*([0-9a-fA-F]+):\s?(.*?)\s*$", re.S)


class Parser:
    def __init__(self, text):
        self.text = text
        self.toks = tokenize(text)
        self.i = 0
        self.types = set(TYPE_WORDS)
        self.last_line = 0

    # tokens; comments are skipped except where the statement lists look for them
    def _skip(self, i):
        while i < len(self.toks) and self.toks[i].kind == "comment":
            i += 1
        return i

    def peek(self, k=0):
        i = self._skip(self.i)
        for _ in range(k):
            i = self._skip(i + 1)
        return self.toks[i] if i < len(self.toks) else None

    def peek_raw(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self):
        self.i = self._skip(self.i)
        if self.i >= len(self.toks):
            raise ParseError("unexpected end")
        t = self.toks[self.i]
        self.i += 1
        self.last_line = t.line + t.text.count("\n")
        return t

    def at(self, text, k=0):
        t = self.peek(k)
        return t is not None and t.text == text and t.kind in ("op", "id")

    def expect(self, text):
        t = self.next()
        if t.text != text:
            raise ParseError("line %d: %r where %r belongs" % (t.line, t.text, text))
        return t

    def ident(self):
        t = self.next()
        if t.kind != "id":
            raise ParseError("line %d: %r where a name belongs" % (t.line, t.text))
        return t.text

    # top level
    def file(self):
        items = []
        while self.i < len(self.toks):
            t = self.toks[self.i]
            if t.kind in ("comment", "pp"):
                items.append(Raw(t.text))
                self.i += 1
                continue
            start, j, depth = t.start, self.i, 0
            while j < len(self.toks):          # to the ';' or the '{' that ends the head, at depth 0
                x = self.toks[j]
                if x.kind == "op" and x.text in "([":
                    depth += 1
                elif x.kind == "op" and x.text in ")]":
                    depth -= 1
                elif depth == 0 and x.kind == "op" and x.text in (";", "{"):
                    break
                j += 1
            if j >= len(self.toks):
                raise ParseError("line %d: a declaration without its end" % t.line)
            if self.toks[j].text == "{" and j > self.i and self.toks[j - 1].text == "=":
                j = self._typedef_end(j)        # (an initializer in braces: to its ';')
            if self.toks[j].text == ";" or self.toks[self.i].text == "typedef":
                if self.toks[self.i].text == "typedef":
                    j = self._typedef_end(self.i)
                    self.types.add(self.toks[j - 1].text)
                items.append(Raw(self.text[start:self.toks[j].end]))
                self.i = j + 1
                continue
            items.append(self.function(j))
        return File(items)

    def _typedef_end(self, i):
        depth = 0
        while i < len(self.toks):
            x = self.toks[i]
            if x.text in ("{", "(", "["):
                depth += 1
            elif x.text in ("}", ")", "]"):
                depth -= 1
            elif x.text == ";" and depth == 0:
                return i
            i += 1
        raise ParseError("a declaration without its end")

    def function(self, brace):
        first = self.toks[self.i]
        head_toks = self.toks[self.i:brace]
        k, depth = None, 0                  # the first '(' at depth 0 behind a name: the parameters
        for n, x in enumerate(head_toks):   # (not an attribute's: void __attribute__((interrupt("IRQ"))) f(void))
            if x.text == "(":
                if depth == 0 and n > 0 and head_toks[n - 1].kind == "id" and head_toks[n - 1].text != "__attribute__":
                    k = n
                    break
                depth += 1
            elif x.text == ")":
                depth -= 1
        if k is None:
            raise ParseError("line %d: a function head without its parameters" % first.line)
        name = head_toks[k - 1].text
        params, depth, cur = [], 0, []
        for x in head_toks[k + 1:]:
            if x.text == "(":
                depth += 1
            elif x.text == ")":
                if depth == 0:
                    break
                depth -= 1
            if x.text == "," and depth == 0:
                params.append(cur)
                cur = []
            else:
                cur.append(x)
        if cur:
            params.append(cur)
        params = [(" ".join(t.text for t in p[:-1]), p[-1].text) for p in params
                  if p and not (len(p) == 1 and p[0].text == "void")]
        head = self.text[first.start:self.toks[brace - 1].end]
        self.i = brace
        body = self.block()
        return Function(head, name, params, body, raw=self.text[first.start:self.toks[self.i - 1].end])

    # statements
    def block(self):
        open_ = self.expect("{")
        note = self._head_note()
        body = []
        while True:
            t = self.peek_raw()
            if t is None:
                raise ParseError("line %d: a block without its end" % open_.line)
            if t.kind == "op" and t.text == "}":
                self.i += 1
                self.last_line = t.line
                break
            if t.kind == "comment":
                self.i += 1
                self._comment(body, t)
                continue
            body.append(self.statement())
        b = Block(body, note)
        b.line, b.end_line = open_.line, self.last_line
        return b

    def _head_note(self):
        """The instruction of a comment right behind a compound statement's head, on its line (the
        printer puts it there when the statement takes more than a line), or None."""
        t = self.peek_raw()
        if t is not None and t.kind == "comment" and t.line == self.last_line and t.text.startswith("/*"):
            m = ADDRESS.match(t.text[2:-2])
            if m:
                self.i += 1
                return (int(m.group(1), 16), m.group(2))
        return None

    def _comment(self, body, t):
        """A comment in a statement list: the address and text of the instruction whose statements
        end on its line, or a note of its own."""
        m = ADDRESS.match(t.text[2:-2]) if t.text.startswith("/*") else None
        mine = [s for s in body if getattr(s, "end_line", None) == t.line and s.addr is None] if m else []
        if mine:
            for s in mine:
                s.addr = (int(m.group(1), 16), m.group(2))
            return
        note = Note(t.text, (int(m.group(1), 16), m.group(2)) if m else None)
        note.line = note.end_line = t.line
        body.append(note)

    def statement(self):
        t = self.peek()
        line = t.line
        s = self._statement(t)
        s.line, s.end_line = line, self.last_line
        return s

    def _statement(self, t):
        x = t.text
        if t.kind == "op" and x == "{":
            return self.block()
        if t.kind == "op" and x == ";":
            self.next()
            return Empty()
        if t.kind == "id":
            if x == "if":
                self.next()
                self.expect("(")
                cond = self.expr()
                self.expect(")")
                note = self._head_note()
                then = self.statement()
                orelse = None
                if self.at("else"):
                    self.next()
                    orelse = self.statement()
                return If(cond, then, orelse, note)
            if x == "goto":
                self.next()
                label = self.ident()
                self.expect(";")
                return Goto(label)
            if x == "return":
                self.next()
                e = None if self.at(";") else self.expr()
                self.expect(";")
                return Return(e)
            if x == "while":
                self.next()
                self.expect("(")
                cond = self.expr()
                self.expect(")")
                note = self._head_note()
                return While(cond, self.statement(), note)
            if x == "do":
                self.next()
                note = self._head_note()
                body = self.statement()
                if self.ident() != "while":
                    raise ParseError("line %d: do without while" % t.line)
                self.expect("(")
                cond = self.expr()
                self.expect(")")
                self.expect(";")
                return DoWhile(body, cond, note)
            if x == "for":
                self.next()
                self.expect("(")
                if self.at(";"):
                    init = None
                    self.next()
                elif self.is_type_start():
                    init = self.declaration()
                else:
                    init = self.expr()
                    self.expect(";")
                cond = None if self.at(";") else self.expr()
                self.expect(";")
                step = None if self.at(")") else self.expr()
                self.expect(")")
                note = self._head_note()
                return For(init, cond, step, self.statement(), note)
            if x == "switch":
                self.next()
                self.expect("(")
                e = self.expr()
                self.expect(")")
                note = self._head_note()
                return Switch(e, self.statement(), note)
            if x == "case":
                self.next()
                value = self.cond_expr()
                self.expect(":")
                return Case(value)
            if x == "default":
                self.next()
                self.expect(":")
                return Case(None)
            if x == "break":
                self.next()
                self.expect(";")
                return Break()
            if x == "continue":
                self.next()
                self.expect(";")
                return Continue()
            if x in ("__asm__", "asm", "__asm"):
                return self.asm()
            if self.at(":", 1) and x not in self.types:
                self.next()
                self.next()
                if self.at(";"):                # (the lifters' "L_1c70: ;")
                    self.next()
                return Label(x)
            if self.is_type_start():
                return self.declaration()
        e = self.expr()
        self.expect(";")
        return ExprStmt(e)

    def is_type_start(self, k=0):
        t = self.peek(k)
        return t is not None and t.kind == "id" and (t.text in self.types or t.text == "__attribute__")

    def _raw_until_close(self):
        """The text from the current '(' to its ')', both included."""
        t = self.next()
        if t.text != "(":
            raise ParseError("line %d: %r where '(' belongs" % (t.line, t.text))
        depth, start = 1, t.start
        while depth:
            x = self.next()
            if x.text == "(":
                depth += 1
            elif x.text == ")":
                depth -= 1
        return self.text[start:x.end]

    def attributes(self):
        out = []
        while self.at("__attribute__"):
            self.next()
            out.append("__attribute__" + self._raw_until_close())
        return " ".join(out)

    def declaration(self):
        specs = []
        while True:
            t = self.peek()
            if t.kind == "id" and t.text == "__attribute__":
                specs.append(self.attributes())
            elif t.kind == "id" and t.text in self.types and not (specs and t.text not in TYPE_WORDS
                                                                   and self._has_base(specs)):
                specs.append(self.next().text)
            else:
                break
        items = []
        while True:
            ptr = ""
            while self.at("*") or (ptr and self.peek().text in ("const", "volatile", "restrict", "__restrict")):
                ptr += self.next().text if self.peek().text == "*" else " %s " % self.next().text
            name = self.ident()
            dims = []
            while self.at("["):
                self.next()
                dims.append(None if self.at("]") else self.expr())
                self.expect("]")
            asm = ""
            if self.peek().text in ("__asm__", "asm", "__asm"):
                self.next()
                asm = "__asm__" + self._raw_until_close()
            attrs = self.attributes()
            init = None
            if self.at("="):
                self.next()
                init = self.initializer()
            items.append(Declarator(name, ptr.strip(), dims, attrs, init, asm))
            if self.at(","):
                self.next()
                continue
            break
        self.expect(";")
        return Decl(" ".join(specs), items)

    def _has_base(self, specs):
        return any(s in self.types and s not in ("const", "volatile", "static", "inline", "register",
                                                 "extern", "auto", "__inline__", "__volatile__") for s in specs)

    def initializer(self):
        if self.at("{"):
            self.next()
            items = []
            while not self.at("}"):
                designator = ""
                while self.at(".") or self.at("["):
                    if self.next().text == ".":
                        designator += "." + self.ident()
                    else:
                        designator += "[%s]" % expr_text(self.cond_expr())
                        self.expect("]")
                if designator:
                    self.expect("=")
                    items.append(Designated(designator, self.initializer()))
                else:
                    items.append(self.initializer())
                if self.at(","):
                    self.next()
            self.expect("}")
            return InitList(items)
        return self.assign_expr()

    def asm(self):
        self.next()
        quals = []
        while self.peek().text in ("volatile", "__volatile__", "goto", "inline"):
            quals.append(self.next().text)
        self.expect("(")
        template = self.string()
        sections = [[], [], [], []]
        part = 0
        while self.at(":"):
            self.next()
            part += 1
            if part > 4:
                raise ParseError("an asm statement with more than four parts")
            while not self.at(":") and not self.at(")"):
                if part == 3:
                    sections[2].append(self.string())
                elif part == 4:
                    sections[3].append(self.ident())
                else:
                    name = None
                    if self.at("["):
                        self.next()
                        name = self.ident()
                        self.expect("]")
                    constraint = self.string()
                    self.expect("(")
                    e = self.expr()
                    self.expect(")")
                    sections[part - 1].append(Operand(constraint, e, name))
                if self.at(","):
                    self.next()
        self.expect(")")
        self.expect(";")
        return Asm(quals, template, sections[0], sections[1], sections[2], sections[3] if part >= 4 else None)

    def string(self):
        t = self.next()
        if t.kind != "str":
            raise ParseError("line %d: %r where a string belongs" % (t.line, t.text))
        parts = [t.text]
        while self.peek() is not None and self.peek().kind == "str":
            parts.append(self.next().text)
        return Str(parts)

    # expressions
    def expr(self):
        e = self.assign_expr()
        if not self.at(","):
            return e
        items = [e]
        while self.at(","):
            self.next()
            items.append(self.assign_expr())
        return Comma(items)

    def assign_expr(self):
        lhs = self.cond_expr()
        t = self.peek()
        if t is not None and t.kind == "op" and t.text in ASSIGN_OPS:
            self.next()
            return Assign(t.text, lhs, self.assign_expr())
        return lhs

    def cond_expr(self):
        c = self.binary(4)
        if self.at("?"):
            self.next()
            t = self.expr()
            self.expect(":")
            return Cond(c, t, self.cond_expr())
        return c

    def binary(self, least):
        left = self.unary()
        while True:
            t = self.peek()
            if t is None or t.kind != "op" or t.text not in BINARY or BINARY[t.text] < least:
                return left
            self.next()
            left = Binary(t.text, left, self.binary(BINARY[t.text] + 1))

    def type_name(self):
        """A type name in parentheses (a cast's, sizeof's), as written with spaces normalised."""
        raw = self._raw_until_close()[1:-1]
        return " ".join(t.text for t in tokenize(raw)).replace(" *", " *").replace("( * )", "(*)")

    def unary(self):
        t = self.peek()
        if t is None:
            raise ParseError("unexpected end in an expression")
        if t.kind == "op" and t.text in ("-", "+", "~", "!", "*", "&"):
            self.next()
            return Unary(t.text, self.unary())
        if t.kind == "op" and t.text in ("++", "--"):
            self.next()
            return Unary(t.text, self.unary())
        if t.kind == "id" and t.text == "sizeof":
            self.next()
            if self.at("(") and self.is_type_start(1):
                return Sizeof(type=self.type_name())
            return Sizeof(e=self.unary())
        if t.kind == "op" and t.text == "(" and self.is_type_start(1):
            ty = self.type_name()
            if self.at("{"):                    # a compound literal, then its postfix operators
                return self.postfix(Compound(ty, self.initializer()))
            return Cast(ty, self.unary())
        return self.postfix()

    def postfix(self, e=None):
        e = self.primary() if e is None else e
        while True:
            t = self.peek()
            if t is None or t.kind != "op":
                return e
            if t.text == "(":
                self.next()
                args = []
                while not self.at(")"):
                    args.append(self.assign_expr())
                    if self.at(","):
                        self.next()
                self.expect(")")
                e = Call(e, args)
            elif t.text == "[":
                self.next()
                i = self.expr()
                self.expect("]")
                e = Index(e, i)
            elif t.text in (".", "->"):
                self.next()
                e = Member(e, self.ident(), t.text == "->")
            elif t.text in ("++", "--"):
                self.next()
                e = Post(t.text, e)
            else:
                return e

    def primary(self):
        t = self.next()
        if t.kind == "id":
            return Name(t.text)
        if t.kind == "num":
            return Num(t.text)
        if t.kind == "chr":
            return Num(t.text, value=ord(ast.literal_eval(t.text)[:1] or "\0"))
        if t.kind == "str":
            parts = [t.text]
            while self.peek() is not None and self.peek().kind == "str":
                parts.append(self.next().text)
            return Str(parts)
        if t.kind == "op" and t.text == "(":
            if self.at("{"):
                raise ParseError("line %d: a statement expression" % t.line)
            e = self.expr()
            self.expect(")")
            return e
        raise ParseError("line %d: %r in an expression" % (t.line, t.text))


def parse(text):
    """The File of a lifted C file (ParseError when it is not the dialect)."""
    return Parser(text).file()


# --- the printer ---

PREC = dict(BINARY)


def expr_prec(e):
    if isinstance(e, Comma):
        return 1
    if isinstance(e, Assign):
        return 2
    if isinstance(e, Cond):
        return 3
    if isinstance(e, Binary):
        return PREC[e.op]
    if isinstance(e, (Unary, Cast, Sizeof)):
        return 14
    if isinstance(e, (Post, Call, Index, Member, Compound)):
        return 15
    return 16


def expr_text(e, least=0):
    s = _expr(e)
    return "(" + s + ")" if expr_prec(e) < least else s


def _expr(e):
    if isinstance(e, Name):
        return e.id
    if isinstance(e, Num):
        return e.text
    if isinstance(e, Str):
        return " ".join(e.parts)
    if isinstance(e, Binary):
        p = PREC[e.op]
        return "%s %s %s" % (expr_text(e.l, p), e.op, expr_text(e.r, p + 1))
    if isinstance(e, Assign):
        return "%s %s %s" % (expr_text(e.target, 14), e.op, expr_text(e.value, 2))
    if isinstance(e, Cond):
        return "%s ? %s : %s" % (expr_text(e.c, 4), expr_text(e.t, 1), expr_text(e.f, 3))
    if isinstance(e, Unary):
        inner = expr_text(e.e, 14)
        if inner[:1] in ("-", "+", "&", "*") and inner[:1] == e.op[-1:]:
            inner = " " + inner             # (- -x, & &x)
        return e.op + inner
    if isinstance(e, Post):
        return expr_text(e.e, 15) + e.op
    if isinstance(e, Cast):
        return "(%s)%s" % (e.type, expr_text(e.e, 14))
    if isinstance(e, Call):
        return "%s(%s)" % (expr_text(e.f, 15), ", ".join(expr_text(a, 2) for a in e.args))
    if isinstance(e, Index):
        return "%s[%s]" % (expr_text(e.base, 15), expr_text(e.index))
    if isinstance(e, Member):
        return "%s%s%s" % (expr_text(e.base, 15), "->" if e.arrow else ".", e.name)
    if isinstance(e, Comma):
        return ", ".join(expr_text(x, 2) for x in e.items)
    if isinstance(e, Sizeof):
        return "sizeof(%s)" % e.type if e.type is not None else "sizeof %s" % expr_text(e.e, 14)
    if isinstance(e, InitList):
        return "{ %s }" % ", ".join(expr_text(x, 2) for x in e.items)
    if isinstance(e, Designated):
        return "%s = %s" % (e.designator, expr_text(e.value, 2))
    if isinstance(e, Compound):
        return "(%s)%s" % (e.type, _expr(e.init))
    raise TypeError("not an expression: %r" % (e,))


def decl_text(d):
    items = []
    for x in d.items:
        s = x.ptr + x.name + "".join("[%s]" % ("" if dim is None else expr_text(dim)) for dim in x.dims)
        if x.asm:
            s += " " + x.asm
        if x.attrs:
            s += " " + x.attrs
        if x.init is not None:
            s += " = " + expr_text(x.init, 2)
        items.append(s)
    return "%s %s;" % (d.specs, ", ".join(items))


def asm_text(a):
    def ops(xs):
        return ", ".join("%s%s(%s)" % ("[%s] " % o.name if o.name else "", " ".join(o.constraint.parts),
                                       expr_text(o.e)) for o in xs)
    parts = [" ".join(a.template.parts)]
    tail = [ops(a.outputs), ops(a.inputs), ", ".join(" ".join(c.parts) for c in a.clobbers)]
    if a.labels is not None:
        tail.append(", ".join(a.labels))
    while tail and not tail[-1] and a.labels is None:
        tail.pop()
    text = parts[0] + "".join(" : " + t if t else " :" for t in tail)
    return "__asm__ %s(%s);" % ("".join(q + " " for q in a.quals), text)


def open_if(s):
    """Whether a statement ends in an if without an else (an else after it would be that if's)."""
    if isinstance(s, If):
        return s.orelse is None or open_if(s.orelse)
    if isinstance(s, (While, For)):
        return open_if(s.body)
    return False


def note_text(addr):
    return "/* %x: %s */" % addr if addr else ""


class Printer:
    """unparse's state: lines of text, the current indentation. notes: the instructions' comments
    (the addresses and texts the statements came from), one per line, as the lifters print them."""

    def __init__(self, notes=True, indent="    "):
        self.notes, self.unit = notes, indent
        self.lines = []

    def emit(self, depth, text, addr=None):
        note = note_text(addr) if self.notes and addr else ""
        self.lines.append((self.unit * depth + text + ("  " + note if note else "")).rstrip())

    def simple(self, s):
        """The one-line text of a statement without a nested statement list, or None."""
        if isinstance(s, ExprStmt):
            return expr_text(s.e) + ";"
        if isinstance(s, Decl):
            return decl_text(s)
        if isinstance(s, Goto):
            return "goto %s;" % s.label
        if isinstance(s, Return):
            return "return;" if s.e is None else "return %s;" % expr_text(s.e)
        if isinstance(s, Break):
            return "break;"
        if isinstance(s, Continue):
            return "continue;"
        if isinstance(s, Empty):
            return ";"
        if isinstance(s, Asm):
            return asm_text(s)
        if isinstance(s, If) and s.orelse is None:
            inner = self.simple(s.then)
            if inner is not None and not isinstance(s.then, If):
                return "if (%s) %s" % (expr_text(s.cond), inner)
        if isinstance(s, Block) and all(self.simple(x) is not None and not isinstance(x, (Label, Case, Note))
                                        for x in s.body) and len(s.body) <= 8:
            return "{ %s }" % " ".join(self.simple(x) for x in s.body) if s.body else "{ }"
        if isinstance(s, For) and isinstance(s.body, (Empty, Block)) and (
                isinstance(s.body, Empty) or not s.body.body):
            return "%s%s" % (self.for_head(s), ";" if isinstance(s.body, Empty) else " { }")
        return None

    def for_head(self, s):
        init = "" if s.init is None else (decl_text(s.init)[:-1] if isinstance(s.init, Decl) else expr_text(s.init))
        cond = "" if s.cond is None else " " + expr_text(s.cond)
        step = "" if s.step is None else " " + expr_text(s.step)
        return "for (%s;%s;%s)" % (init, cond, step)

    def stmts(self, body, depth):
        """Statements, those of one instruction (one address) on one line, as the lifters write them."""
        i = 0
        while i < len(body):
            s = body[i]
            if isinstance(s, Label):
                self.lines.append("%s: ;" % s.name + ("  " + note_text(s.addr) if self.notes and s.addr else ""))
                i += 1
                continue
            if isinstance(s, Case):
                self.emit(max(depth - 1, 0), "default:" if s.value is None else "case %s:" % expr_text(s.value),
                          s.addr)
                i += 1
                continue
            if isinstance(s, Note):
                if self.notes:
                    self.emit(depth, s.text if s.addr is None else note_text(s.addr))
                i += 1
                continue
            text = self.simple(s)
            if text is not None:
                group = [text]
                j = i + 1
                while s.addr is not None and j < len(body) and body[j].addr == s.addr and \
                        not isinstance(body[j], (Label, Case, Note)) and self.simple(body[j]) is not None:
                    group.append(self.simple(body[j]))
                    j += 1
                self.emit(depth, " ".join(group), s.addr)
                i = j
                continue
            self.compound(s, depth)
            i += 1

    def compound(self, s, depth):
        if isinstance(s, Block):
            self.emit(depth, "{", s.addr)
            self.stmts(s.body, depth + 1)
            self.emit(depth, "}")
        elif isinstance(s, If):
            self.emit(depth, "if (%s)" % expr_text(s.cond), s.addr)
            then = s.then
            if s.orelse is not None and open_if(then):
                then = Block([then])            # (the else is this if's, not the inner one's)
            self.body(then, depth)
            if s.orelse is not None:
                self.emit(depth, "else")
                self.body(s.orelse, depth)
        elif isinstance(s, While):
            self.emit(depth, "while (%s)" % expr_text(s.cond), s.addr)
            self.body(s.body, depth)
        elif isinstance(s, DoWhile):
            self.emit(depth, "do", s.addr)
            self.body(s.body, depth)
            self.emit(depth, "while (%s);" % expr_text(s.cond))
        elif isinstance(s, For):
            self.emit(depth, self.for_head(s), s.addr)
            self.body(s.body, depth)
        elif isinstance(s, Switch):
            self.emit(depth, "switch (%s)" % expr_text(s.e), s.addr)
            self.body(s.body, depth)
        else:
            raise TypeError("not a statement: %r" % (s,))

    def body(self, s, depth):
        if isinstance(s, Block):
            self.emit(depth, "{")
            self.stmts(s.body, depth + 1)
            self.emit(depth, "}")
        else:
            self.stmts([s], depth + 1)


def unparse(tree, notes=True, verbatim=()):
    """C text of a File, a Function or a statement. notes: the instructions' comments; verbatim:
    names of functions printed as they were written (helpers of the lifted file)."""
    p = Printer(notes)
    if isinstance(tree, File):
        out = []
        for item in tree.items:
            if isinstance(item, Raw):
                out.append(item.text)
            elif item.name in verbatim:
                out.append(item.raw)
            else:
                out.append(unparse(item, notes))
        return "\n".join(out) + "\n"
    if isinstance(tree, Function):
        p.lines.append(tree.head)
        p.emit(0, "{")
        p.stmts(tree.body.body, 1)
        p.emit(0, "}")
        return "\n".join(p.lines)
    p.stmts([tree] if isinstance(tree, Stmt) else list(tree), 0)
    return "\n".join(p.lines)
