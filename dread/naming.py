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
naming a global later does not invalidate them.

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
    if m and new_params != fn.params:
        fn.head = "%s %s(%s)" % (m.group(1), m.group(2), ", ".join("%s %s" % p for p in new_params) or "void")
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


def _rewrite_stmt(s, f):
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
    for inner in _inner(s):
        _rewrite_stmt(inner, f)


def apply_globals(tree, fn, named=None, memory=None, avoid=()):
    """The function's accesses to memory at fixed addresses written as names, a macro for each above
    the function (#define g_20029aac (*(volatile u32 *)0x20029aacu)): the table's name (named: the
    globals part) where it has one the file does not use for something else (nor avoid: the names the
    function's own variables will get), else default_name. Returns {key: name} of what it named."""
    named = named or {}
    found = globals_in(fn)
    if not found:
        return {}
    busy = other_names(tree, fn) | set(variables(fn)) | set(avoid)
    chosen = {}
    for address, ctype in sorted(found):
        name = (named.get(global_key(address, ctype)) or {}).get("name")
        if (not isinstance(name, str) or not IDENT.match(name) or name in busy or name in KEYWORDS
                or name in chosen.values()):
            name = default_name(address, ctype, memory)
        chosen[(address, ctype)] = name

    def f(e):
        a = fixed_access(e)
        return C.Name(chosen[a]) if a in chosen else None
    for s in fn.body.body:
        _rewrite_stmt(s, f)
    k = tree.items.index(fn)
    tree.items.insert(k, C.Raw("\n".join("#define %s (*(volatile %s *)0x%xu)" % (chosen[a], a[1], a[0])
                                         for a in sorted(chosen))))
    return {global_key(*a): n for a, n in chosen.items()}


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
