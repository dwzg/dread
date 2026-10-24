"""Addresses of functions and texts in readable C (dread.naming apply_globals, dread.constants Pointed):
a constant that is a function's entry as code takes its address (bit 0 set for Thumb code) and is
used as an address prints as the function's address, (u32)&name, through a macro of dread.h that is
the same number (#define name FUNCTION_AT(0x...u)); a constant that is a text's start keeps its number
with the text in a note beside it, 0x...u /* "mode " */. Used as an address: an argument for a
parameter the callee uses as a pointer or that takes such addresses (the readable_types table's
takes: every call gives texts, or functions), a function's also what a call goes through and a value
stored into memory; a number that happens to be such an address stays a number."""
import re
import shutil

import pytest

from dread import cparse, naming, polish
from dread.constants import Pointed

needs_gcc = pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")


class Stub:
    """What a Pointed answers, given: {value: function name} and {value: (text, begins there)}."""

    def __init__(self, functions, texts):
        self.functions, self.texts = functions, texts

    def function(self, value):
        return self.functions.get(value)

    def text(self, value):
        return (self.texts[value][0], False) if value in self.texts else None

    def begins(self, value):
        return self.texts[value][1]

    def note(self, value):
        return '"%s"' % self.texts[value][0]


USES = ("/* f at 0x0 */\n#include \"dread.h\"\nextern u32 show(u32);\nextern u32 keep(u32, u32);\n"
        "extern u32 count(u32);\nextern u32 ticker_up(u32);\n"
        "u32 f(u32 r0, u32 r1)\n{\n    u32 r3;\n"
        "    show(0x1200u);\n"
        "    show(0x1203u);\n"
        "    keep(0x1203u, 0x2001u);\n"
        "    count(0x1200u);\n"
        "    count(0x2001u);\n"
        "    *(volatile u32 *)(r0 + 8) = 0x2001u;\n"
        "    *(volatile u32 *)(r0 + 12) = 0x80000000u;\n"
        "    *(volatile u32 *)(r0 + 16) = 0x1200u;\n"
        "    r3 = 0x2001u;\n"
        "    if (r1 == 0x2001u) r0 = r3;\n"
        "    ((u32 (*)(u32))0x2011u)(r0);\n"
        "    keep(0x1200u, 0x2021u);\n"
        "    keep(0x1200u, 0x2031u);\n"
        "    return 0x2001u;\n}\n")
POINTED = Stub({0x2001: "tock", 0x2011: "tick", 0x2021: "ticker_up", 0x2031: "tock.part.0", 0x80000000: "first"},
               {0x1200: ("hello", True), 0x1203: ("lo", False)})


def uses_c(pointers, takes):
    t = cparse.parse(USES)
    naming.apply_globals(t, t.function("f"), None, None, pointers=pointers, pointed=POINTED, takes=takes)
    return cparse.unparse(t, notes=False)


def test_a_function_or_a_text_where_its_use_is_an_address():
    """A text that begins where the constant points, handed to a parameter its callee uses as a pointer,
    gets its note; one inside a longer text (a merged string's end, or a number by chance) only where
    the parameter takes texts; a function's entry for a parameter that takes functions, stored into
    memory, or called through is the function's address, through its macro; a count (a parameter
    neither), a value assigned to a variable, compared or returned, a text stored, and a function at an
    address that looks like a bit pattern (0x80000000) stay numbers. Where the file uses the name
    otherwise (it declares the callee ticker_up) or the name is no C name, the number keeps the name
    in a note."""
    out = uses_c({"show": [0], "keep": [], "count": []}, {"keep": {0: "text", 1: "function"}})
    for line in ('show(0x1200u /* "hello" */);', "show(0x1203u);", 'keep(0x1203u /* "lo" */, (u32)&tock);',
                 "count(0x1200u);", "count(0x2001u);", "*(volatile u32 *)(r0 + 8) = (u32)&tock;",
                 "*(volatile u32 *)(r0 + 12) = 0x80000000u;", "*(volatile u32 *)(r0 + 16) = 0x1200u;",
                 "r3 = 0x2001u;", "if (r1 == 0x2001u) r0 = r3;", "&tick)(r0);",
                 'keep(0x1200u /* "hello" */, 0x2021u /* &ticker_up */);',
                 'keep(0x1200u /* "hello" */, 0x2031u /* &tock.part.0 */);', "return 0x2001u;",
                 "#define tick FUNCTION_AT(0x2011u)", "#define tock FUNCTION_AT(0x2001u)"):
        assert line in out, (line, out)
    assert "#define ticker_up" not in out and "#define first" not in out, out
    # (without the table's evidence: the call through a constant and the store alone)
    bare = uses_c(None, None)
    assert "show(0x1200u);" in bare and "keep(0x1203u, 0x2001u);" in bare, bare
    assert "*(volatile u32 *)(r0 + 8) = (u32)&tock;" in bare and "&tick)(r0);" in bare, bare


def test_what_takes_texts_and_functions():
    """taken_kinds: a parameter given at least two distinct constants, every one a function's entry,
    takes functions; every one a text, more than half of them beginning there (a merged string's end
    among them), takes texts; one constant, or a number among them, or as many texts inside others as
    beginning, is no evidence; a parameter the function calls through (a copy of it) takes functions."""
    text = ("/* t */\n#include \"dread.h\"\nextern u32 show(u32);\nextern u32 keep(u32, u32);\nextern u32 count(u32);\n"
            "extern u32 pick(u32);\n"
            "u32 a(u32 r0)\n{\n    show(0x1200u);\n    keep(0x1200u, 0x2001u);\n    keep(0x1203u, 0x2011u);\n"
            "    count(0x1200u);\n    count(5);\n    pick(0x1200u);\n    pick(0x1203u);\n    return 0;\n}\n"
            "u32 b(u32 r0)\n{\n    keep(0x1210u, 0x2001u);\n    count(0x1210u);\n    return 0;\n}\n"
            "u32 walk(u32 r0, u32 r1)\n{\n    u32 r4;\n    r4 = r1;\n    ((u32 (*)(u32))r4)(r0);\n    return 0;\n}\n")
    tree = cparse.parse(text)
    trees = [(tree, tree.function(n), {}) for n in ("a", "b", "walk")]
    pointed = Stub({0x2001: "tock", 0x2011: "tick"}, {0x1200: ("hello", True), 0x1203: ("lo", False),
                                                     0x1210: ("world", True)})
    assert naming.taken_kinds(trees, pointed) == {"keep": {0: "text", 1: "function"}, "walk": {1: "function"}}


def test_a_note_prints_beside_its_literal_and_keeps_it():
    """A literal with a note prints the note behind it as a comment; it is no other literal (equality),
    the parser reads the comment as nothing, and dread.polish keeps such a literal as it is (0x5u would
    be 5)."""
    tree = cparse.parse("u32 f(u32 r0)\n{\n    r0 = 0x5u;\n    return r0;\n}\n")
    fn = tree.function("f")
    num = fn.body.body[0].e.value
    assert num == cparse.Num("0x5u")
    num.note = '"a */ b"'
    assert num != cparse.Num("0x5u") and cparse.expr_text(num) == '0x5u /* "a */ b" */'
    polish.polish(tree, fn)
    assert '    r0 = 0x5u /* "a */ b" */;' in cparse.unparse(fn, notes=False)
    assert cparse.parse("u32 f(void)\n{\n    return 0x5u /* x */;\n}\n").function("f").body.body[0].e.note is None


POINTED_TOML = """[target]
name = "pointed"
[arch]
name = "arm"
isa = "t32"
profile = "m"
cpu = "cortex-m3"
[memory]
regions = [{name = "flash", base = 0x0, size = 0x10000, kind = "rom"},
           {name = "ram", base = 0x20000000, size = 0x1000}]
stack = {top = 0x20001000, size = 0x100}
sentinel = 0x00f00000
[image.main]
original = "original/t.bin"
base = 0x0
startup = [{kind = "load", run = 0x20000000, size = 16, file = "original/load.bin"},
           {kind = "data", run = 0x20000100, size = 16, stored = 0x300},
           {kind = "code", run = 0x20000200, size = 8, stored = 0x340}]
"""


def pointed_target(tmp_path):
    """A target whose image holds two Thumb functions (0x40 and 0x100), texts at 0x200 ("hello" and
    "world", "lo" inside the first), a path longer than a note shows, texts with a quote and a comment's
    end, a word-aligned text behind a word whose last byte is a space, and what the startup fills: the
    loader's bytes at 0x20000000, a copy of data at 0x20000100 (stored at 0x300) and of code at
    0x20000200 (stored at 0x340)."""
    from dread.program import Program
    from dread.target import Target
    image = bytearray(0x800)
    image[0x40:0x44] = image[0x100:0x104] = b"\x00\xbf\x00\xbf"        # (nop; nop)
    image[0x1ff:0x20c] = b"\x00hello\x00world\x00"
    path = b"/a/long/path/to/the/sources/of/a/demo/" + b"x" * 40 + b"/timers.c"
    image[0x400:0x400 + len(path) + 1] = path + b"\x00"
    image[0x480:0x490] = b'say "hi"*/\x00\r\n\x00\x00\x00'
    image[0x500:0x50c] = b"\x01\x02\x03 aligned\x00"
    image[0x600:0x604] = b"qrs\x00"
    image[0x300:0x30a] = b"data text\x00"
    image[0x340:0x348] = b"\x00\xbf\x00\xbfab\x00\x00"
    (tmp_path / "original").mkdir()
    (tmp_path / "original" / "t.bin").write_bytes(bytes(image))
    (tmp_path / "original" / "load.bin").write_bytes(b"load text\x00" + bytes(6))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "functions.tsv").write_text("address\tname\tbytes\n0x40\tlow\t4\n0x100\ttock\t4\n"
                                                       "0x20000200\tram_code\t4\n")
    (tmp_path / "config" / "code_ranges.tsv").write_text("0x40 0x44\n0x100 0x104\n0x20000200 0x20000204\n")
    (tmp_path / "dread.toml").write_text(POINTED_TOML)
    target = Target.load(str(tmp_path))
    return Program(target, target.image())


def test_what_lies_at_a_constant(tmp_path):
    """Pointed: a Thumb function's entry with bit 0 set is the function, its even entry and an address
    inside it are not, and neither is anything below SMALLEST_POINTER; a text in a rom region is its
    printable characters up to a NUL, where it begins or inside a longer one (begins tells), a word-
    aligned one behind a word whose last byte is a space begins there; a text the loader puts into
    memory or the startup copies there is what that memory holds at the start ("initially"); code
    (at its run address and its stored bytes) is no text; a long text is cut to its start and its end,
    and the note is a C string literal that cannot end its comment."""
    p = Pointed(pointed_target(tmp_path))
    assert p.function(0x101) == "tock" and p.function(0x100) is None and p.function(0x103) is None
    assert p.function(0x41) is None                 # (below SMALLEST_POINTER: a number)
    assert p.text(0x200) == ("hello", False) and p.begins(0x200) and p.note(0x200) == '"hello"'
    assert p.text(0x203) == ("lo", False) and not p.begins(0x203) and p.begins(0x206)
    assert p.text(0x504) == ("aligned", False) and p.begins(0x504)
    assert p.text(0x601) == ("rs", False) and not p.begins(0x601)
    assert p.text(0x100) is None and p.text(0x20000200) is None and p.text(0x344) is None
    assert p.text(0x20000000) == ("load text", True) and p.note(0x20000000) == 'initially "load text"'
    assert p.note(0x20000100) == 'initially "data text"'
    assert p.note(0x480) == '"say \\"hi\\"*\\/"' and p.note(0x48b) == '"\\r\\n"'
    note = p.note(0x400)
    assert note.startswith('"/a/long/path/to') and note.endswith('xxxxxxxx/timers.c"') and "..." in note, note
    assert p.text(0x20000300) is None and p.text(0x700) is None    # (nothing known; zeros)


PROGRAM = """typedef unsigned int u32;
__attribute__((noinline)) u32 text_len(const char *s) { u32 n = 0; while (s[n]) n++; return n; }
struct ticker { const char *name; u32 period; void (*expire)(struct ticker *); u32 runs; };
struct ticker tickers[4];
u32 ticker_count;
void (*idle_hook)(struct ticker *);
void (*wake_hook)(struct ticker *);
__attribute__((noinline)) struct ticker *ticker_make(const char *name, u32 period, void (*expire)(struct ticker *))
{
    struct ticker *t = &tickers[ticker_count++ & 3];
    t->name = name;
    t->period = period;
    t->expire = expire;
    return t;
}
__attribute__((noinline)) void ticker_up(struct ticker *t) { t->runs++; }
__attribute__((noinline)) void ticker_down(struct ticker *t) { t->runs--; }
__attribute__((noinline)) u32 ticker_wait(u32 ticks) { u32 s = 0; while (ticks--) s += ticks; return s; }
void tickers_start(void)
{
    ticker_make("up ticker", 100, ticker_up);
    ticker_make("down ticker", 200, ticker_down);
    idle_hook = ticker_down;
    wake_hook = ticker_up;
}
u32 tickers_wait(void) { return ticker_wait((u32)ticker_down) + ticker_wait((u32)"a count"); }
u32 banner_len(void) { return text_len("banner text"); }
int main(void) { return 0; }
"""


def kinds():
    from test_statics import needs_kind
    return [pytest.param(k, marks=needs_kind(k)) for k in ("a32", "m3", "rv32")]


@pytest.mark.parametrize("kind", kinds())
def test_functions_and_texts_by_name_through_the_gate(kind, tmp_path):
    """A program of our own (GCC -Os for ARM state, Thumb-2 and RISC-V) whose ticker_make only stores
    the name and the function it is given (no pointer for dread.shapes): the two calls make its
    parameters take texts and functions (the readable_types table's takes), so its calls read
    ticker_make(0x...u /* "up ticker" */, 0x64u, (u32)&ticker_up), the macro the function's address as
    code takes it (bit 0 set on the Cortex-M3); text_len, which reads bytes through its parameter, gets
    "banner text" in a note; a function's address stored into memory is the function's, unless it looks
    like a bit pattern (where the image's first function sits at its base: 0x20000, 0x20000000); a
    function's address and a text's handed to a count (ticker_wait) stay numbers. The gate accepts
    every lifted function: the names compile to the same code."""
    from test_statics import kind_target
    from dread import build, lift, readable, regflow
    from dread.program import Program
    target = kind_target(tmp_path, kind, PROGRAM, rodata=True)
    image = target.image()
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert accepted == sorted(build.read_names(image.table("lifted"))) and "tickers_start" in accepted, lines
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    context = readable.Context(target, image, program, flow)
    assert context.plan.takes["ticker_make"] == {0: "text", 2: "function"}, context.plan.takes
    lifter = lift.make_lifter(program, flow)

    def text(name):
        return readable.transform(name, lifter.lift(program.by_name[name]).text, context=context)
    start, wait, banner = text("tickers_start"), text("tickers_wait"), text("banner_len")
    up, down = program.by_name["ticker_up"], program.by_name["ticker_down"]
    for fn in (up, down):
        address = program.arch.start_address(fn.entry, fn.isa)
        assert address & 1 == (kind == "m3")
        assert "#define %s FUNCTION_AT(0x%xu)" % (fn.name, address) in start, start
    assert re.search(r'ticker_make\(0x[0-9a-f]+u /\* "up ticker" \*/, 0x64u, \(u32\)&ticker_up\);', start), start
    assert re.search(r'ticker_make\(0x[0-9a-f]+u /\* "down ticker" \*/, 0xc8u, \(u32\)&ticker_down\);', start), start
    for fn in (up, down):
        address = program.arch.start_address(fn.entry, fn.isa)
        stored = "= 0x%xu;" % address if polish.patterned(address) else "= (u32)&%s;" % fn.name
        assert re.search(r"\bg_[0-9a-f]+(\.f_\w+)? %s" % re.escape(stored), start), (stored, start)
    assert re.search(r'text_len\(0x[0-9a-f]+u /\* "banner text" \*/\)', banner), banner
    assert "ticker_wait(0x%xu)" % program.arch.start_address(down.entry, down.isa) in wait, wait
    assert "/*" not in re.sub(r"  /\* [0-9a-f]+: .*", "", wait.split("{", 1)[1]) and "&" not in wait, wait


def test_gizmo_shows_its_texts_beside_their_addresses(target):
    """gizmo's print_status and main on every build (the review's public example): uart_puts reads bytes
    through its parameter and every call gives it texts, so its calls read uart_puts(0x...u /* "mode "
    */), and main hands apply_config the default configuration with its text in the note; the printed
    form and the reading form show the notes as the compiled form does."""
    from dread import build, lift, readable, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    context = readable.Context(target, image, program, flow)
    lifter = lift.make_lifter(program, flow)
    readable.make_plan(program, context, lifter, build.read_names(image.table("lifted")), flow)
    assert context.plan.takes.get("uart_puts") == {0: "text"}, context.plan.takes

    def lifted(name):
        return lifter.lift(program.by_name[name]).text
    status = readable.transform("print_status", lifted("print_status"), context=context)
    for text in ("mode ", " volume ", "\\r\\n"):
        assert re.search(r'uart_puts\(0x[0-9a-f]+u /\* "%s" \*/\)' % re.escape(text), status), (text, status)
    main = readable.transform("main", lifted("main"), context=context)
    assert re.search(r'apply_config\(0x[0-9a-f]+u /\* "volume = 7\\nmode=2\\n  baud = 0x2580\\nname=3\\n" '
                     r'\*/\);', main), main
    assert re.search(r'uart_puts\(0x[0-9a-f]+u /\* "GIZMO 1000\\r\\n" \*/\);', main), main
    assert '/* "mode " */' in readable.printed("print_status", lifted("print_status"), context=context)
    assert any('/* "mode " */' in line for _, line in readable.reading("print_status", lifted("print_status"),
                                                                       context=context))


@needs_gcc
def test_a_name_question_notes_each_text_once(tmp_path):
    """dread pilot's questions for function names follow each constant with what it points to; a text
    the readable C notes already (with the check's plan: strcmp takes texts) is noted once, and nothing
    inside a note is noted again."""
    from conftest import GIZMO
    from dread import build, lift, pilot, readable, regflow, shapes
    from dread.program import Program
    from dread.target import Target
    shutil.copytree(GIZMO, tmp_path / "gizmo", ignore=shutil.ignore_patterns("build"))
    target = Target.load(str(tmp_path / "gizmo" / "m3"))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    context = readable.Context(target, image, program, flow)
    readable.make_plan(program, context, lift.make_lifter(program, flow), build.read_names(image.table("lifted")),
                       flow)
    shapes.store_plan(image, context.plan)
    text, form = pilot.Namer(target, image, program, flow).question(program.by_name["apply_config"])
    assert form.startswith("readable") and text.count('/* "volume" */') == 1, text
    text, form = pilot.Namer(target, image, program, flow).question(program.by_name["main"])
    assert text.count('"volume = 7\\nmode=2\\n  baud = 0x2580\\nname=3\\n"') == 1 and "*/ */" not in text, text
