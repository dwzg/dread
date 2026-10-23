"""dread.statics: structures of static data at fixed addresses, found from the machine code. What one
base reaches at constant offsets is a structure where the code reaches its neighbours through bases of
their own (GCC with -fdata-sections); GCC's section anchor, the one base a file reaches all of its
variables through (without -fdata-sections), is none, and an indexed table behind it is an array that
starts at its constant part; an address handed out and the target's data symbols end a structure.
Readable C writes a structure's fields as members of one object (g_20000030.f_4), every access one of
its width at its address. An access at a constant plus an index times a scale (readable C's) says
that an array of elements of that size lies there: its elements are written as such (g_x[r3].f_4,
g_x[1]), also where the index went through a variable that holds it times the scale, and the
addresses of objects, fields and elements the code uses as values as &-expressions."""
import re
import shutil
import subprocess

import pytest

from dread import cmetrics, cparse, naming, shapes, statics

needs_gcc = pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")

TOML = """[target]
name = "statics"
[arch]
name = "arm"
isa = "t32"
profile = "m"
cpu = "cortex-m3"
[memory]
regions = [{name = "flash", base = 0x08000000, size = 0x10000, kind = "rom"},
           {name = "ram", base = 0x20000000, size = 0x10000}]
stack = {top = 0x20010000, size = 0x1000}
sentinel = 0x00f00000
[image.main]
original = "original/t.bin"
base = 0x08000000
[compiler]
cross = "arm-none-eabi-"
cflags = ["-mthumb", "-mcpu=cortex-m3", "-Os"]
"""
LD = ("SECTIONS { . = 0x08000000; .text : { *(.text*) *(.rodata*) } . = 0x20000000; .data : { *(.data*) } "
      ".bss : { *(.bss*) *(COMMON) } }\n")
HEAD = "typedef unsigned int u32;\ntypedef unsigned short u16;\ntypedef unsigned char u8;\n"

QUILL = HEAD + """struct quill { u32 count; u16 level; u8 flags; u8 spare; u32 total; };
struct quill quill_state;
u32 quill_hits;
u32 quill_last;
void quill_feed(u32 v) { quill_state.count++; quill_state.total += v; quill_last = v; }
void quill_mark(u32 l) { quill_state.level = (u16)l; quill_state.flags |= 1; quill_hits++; }
u32 quill_sum(void) { return quill_state.count + quill_state.total + quill_state.level; }
int main(void) { return 0; }
"""

# A file whose variables GCC reaches from one section anchor: a structure first, then three variables
# only this file uses and one another file reaches through its own address; a table read with an index
WREN_A = HEAD + """struct wren { u32 rate; u16 level; u16 flags; u32 limit; };
struct wren wren_cfg;
static u32 wren_ticks;
static u16 wren_errs;
static u32 wren_samples[4];
u32 wren_seen;
static const u16 wren_steps[4] = { 3, 5, 9, 17 };
void wren_fill(u32 *p, u32 n);
u32 wren_cfg_sum(void) { return wren_cfg.rate + wren_cfg.level + wren_cfg.limit; }
void wren_cfg_set(u32 r, u32 l) { wren_cfg.rate = r; wren_cfg.flags = (u16)l; }
u32 wren_tick(void) { wren_ticks++; wren_errs |= 1; return wren_samples[1] + wren_seen; }
void wren_reset(void) { wren_ticks = 0; wren_seen = 0; wren_fill(wren_samples, 4); }
u32 wren_step(u32 i) { return wren_steps[i & 3]; }
"""
WREN_B = HEAD + """extern u32 wren_seen;
void wren_fill(u32 *p, u32 n) { while (n--) *p++ = 0; }
u32 wren_add(u32 x) { wren_seen += x; return wren_seen; }
int main(void) { return 0; }
"""


def built(tmp_path, sources, flags=()):
    """(program, evidence) of a target made from C sources ({file: text}) compiled with GCC for a
    Cortex-M3 (-Os, flags added), imported from its ELF (its data symbols too)."""
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    for name, text in sources.items():
        (tmp_path / name).write_text(text)
    (tmp_path / "t.ld").write_text(LD)
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mthumb", "-mcpu=cortex-m3", "-Os", "-ffreestanding", "-nostdlib",
                    "-fno-common", "-ffunction-sections"] + list(flags) + ["-T", str(tmp_path / "t.ld"),
                    "-Wl,-e,main", "-o", elf] + [str(tmp_path / n) for n in sources], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TOML)
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    target = Target.load(str(tmp_path))
    program = Program(target, target.image())
    return program, statics.gather(program)


def data(program):
    return {name: a for kind, a, name in program.symbols() if kind == "d"}


@needs_gcc
def test_a_structure_one_base_reaches_where_neighbours_have_bases_of_their_own(tmp_path):
    """GCC with -fdata-sections makes a base per object: quill_state's fields come from its base in
    three functions, and two of them reach quill_hits and quill_last next to it through bases of
    their own, which shows that the base is the structure's and no anchor of the file's variables."""
    program, ev = built(tmp_path, {"q.c": QUILL}, ["-fdata-sections"])
    at = data(program)["quill_state"]
    found = statics.plan(program, ev, symbols=False)
    assert found == {at: [(0, "u32"), (4, "u16"), (6, "u8"), (8, "u32")]}, found
    assert statics.describe(found) == ["0x%08x   12 bytes: 0:u32, 4:u16, 6:u8, 8:u32" % at]


@needs_gcc
def test_a_section_anchor_is_no_structure(tmp_path):
    """Without -fdata-sections GCC reaches every variable of wren_a.c from one anchor: its runs of
    variables look like a structure's fields, but the functions that use the anchor reach nothing next
    to it apart from it, so the code alone makes no structure. Taken anyway (trust="all"), a structure
    holds several of the file's variables; the data symbols end every structure at its variable's end.
    The table read with an index is an array that starts at its constant part (the anchor plus the
    table's offset), and wren_samples' address, handed to wren_fill, ends what lies in front of it.
    (Where GCC puts each variable in the anchor's block differs between its versions: only what holds
    whatever the order is asserted.)"""
    program, ev = built(tmp_path, {"wren_a.c": WREN_A, "wren_b.c": WREN_B})
    at = data(program)
    starts = sorted(at.values())
    names = {a: n for n, a in at.items()}

    def variables(found):
        """{structure: the variables its fields lie in} (by the data symbols)."""
        out = {}
        for base, rows in found.items():
            out[base] = {names[max(s for s in starts if s <= base + off)] for off, _ in rows}
        return out
    assert statics.plan(program, ev, symbols=False) == {}
    every = variables(statics.plan(program, ev, symbols=False, trust="all"))
    assert any(len(v) > 1 for v in every.values()), every                  # (a file's variables: wrong)
    assert all(len(v) == 1 for v in variables(statics.plan(program, ev, symbols=True, trust="all")).values())
    assert at["wren_samples"] in ev["wren_reset"].escapes                   # (handed out: something starts there)
    steps = [a for a, t, origin, ix in ev["wren_step"].accesses if ix]
    assert steps == [at["wren_steps"]], ev["wren_step"].accesses


def test_the_layout_aligns_every_field_and_takes_the_most_frequent_size():
    """A field is naturally aligned at its offset and its address; a place read as a word and as its
    low byte is the word (the most frequent size), its byte read stays as it is; a field that would
    overlap the one before is left out."""
    fields = {0: {"u32": 3, "u8": 1}, 4: {"u16": 1}, 6: {"s8": 2}, 7: {"u32": 1}, 8: {"u16": 1, "u32": 1}}
    assert statics.layout(fields, 0x20000000) == [(0, "u32"), (4, "u16"), (6, "s8"), (8, "u32")]
    assert statics.layout({0: {"u16": 1}, 2: {"u32": 1}}, 0x20000002) == [(0, "u16")]   # (2 is no word's offset)


def test_a_plan_keeps_its_structures_of_static_data():
    p = shapes.Plan()
    p.statics = {0x20000030: [(0, "u32"), (4, "u16")]}
    data_ = p.to_json()
    assert data_["statics"] == {"0x20000030": [[0, "u32"], [4, "u16"]]}
    assert shapes.Plan.from_json(data_).statics == p.statics
    assert "statics" not in shapes.Plan().to_json()             # (an older reader sees nothing new)
    assert shapes.Plan.from_json({"structs": {}}).statics == {}


FIXED = ("/* f at 0x0 */\n#include \"dread.h\"\nu32 f(u32 r0)\n{\n    u32 r3;\n"
         "    r3 = *(volatile u32 *)0x20000030u + *(volatile u16 *)0x20000034u;\n"
         "    *(volatile u32 *)0x20000038u = r3;\n"
         "    *(volatile u8 *)0x20000030u = 1;\n"
         "    if (*(volatile u32 *)0x20000044u != 0) *(volatile u16 *)0x20000048u = 2;\n"
         "    return *(volatile u32 *)0x20000040u;\n}\n")
PLAN = {0x20000030: [(0, "u32"), (4, "u16"), (8, "u32")], 0x20000044: [(0, "u32"), (4, "u16")]}


def readable_c(named=None, plan=PLAN):
    t = cparse.parse(FIXED)
    fn = t.function("f")
    chosen = naming.apply_globals(t, fn, named, None, statics=plan)
    return cparse.unparse(t, notes=False), chosen


def test_the_fields_of_a_structure_of_static_data_are_members_of_one_object():
    """One macro for the structure, its definition above the function; a byte at its address it has
    no such field for stays a macro of its own, and memory outside every structure too."""
    out, chosen = readable_c()
    assert "r3 = g_20000030.f_0 + g_20000030.f_4;" in out and "g_20000030.f_8 = r3;" in out, out
    assert "g_20000030_u8 = 1;" in out and "return g_20000040;" in out, out
    assert "if (g_20000044.f_0 != 0) g_20000044.f_4 = 2;" in out, out
    assert "#define g_20000030 (*(volatile struct g_20000030_t *)0x20000030u)" in out, out
    assert "struct g_20000030_t\n{\n    u32 f_0;\n    u16 f_4;\n    u8 pad_6[2];\n    u32 f_8;\n};" in out, out
    assert out.index("struct g_20000030_t\n{") < out.index("#define g_20000030 ") < out.index("u32 f(u32 r0)"), out
    assert chosen["0x20000034 u16"] == "g_20000030.f_4" and chosen["0x20000030 u8"] == "g_20000030_u8", chosen
    same, _ = readable_c(plan={})                       # (without a plan: a macro per address and type)
    assert "struct" not in same and "r3 = g_20000030 + g_20000034_u16;" in same, same


def test_a_word_at_a_structure_s_address_it_holds_no_word_for_keeps_a_name_of_its_own():
    out, _ = readable_c(plan={0x20000030: [(0, "u16"), (4, "u16"), (8, "u32")]})
    assert "r3 = g_20000030_u32 + g_20000030.f_4;" in out, out
    assert "#define g_20000030_u32 (*(volatile u32 *)0x20000030u)" in out, out


def test_a_structure_and_its_fields_take_the_table_s_names_where_they_are_free():
    """The structure's entry names it and some fields; a field without one takes the name the table
    gave its address before the plan made it a field; a name another global keeps (a macro: it would
    expand inside the member) or the structure's own is refused for the default."""
    named = {"0x20000030 struct": {"name": "quill", "fields": {"8": "total", "0": "hits"}},
             "0x20000034 u16": {"name": "level"}, "0x20000040 u32": {"name": "hits"},
             "0x20000044 struct": {"name": "quill"}}
    out, _ = readable_c(named)
    assert "r3 = quill.f_0 + quill.level;" in out and "quill.total = r3;" in out, out
    assert "#define quill (*(volatile struct quill_t *)0x20000030u)" in out and "return hits;" in out, out
    assert "if (g_20000044.f_0 != 0)" in out, out          # (quill is taken: the default)
    names = naming.static_names(PLAN, named)
    assert names[0x20000030] == ("quill", "quill_t", {0: "f_0", 4: "level", 8: "total"}), names
    t = cparse.parse(FIXED.replace("u32 r3;", "u32 r3, quill;"))   # (a variable of that name here: the default)
    naming.apply_globals(t, t.function("f"), named, None, statics=PLAN)
    out = cparse.unparse(t, notes=False)
    assert "r3 = g_20000030.f_0 + g_20000030.level;" in out and "struct quill_t\n{" in out, out


def test_the_figures_count_members_of_static_data_apart():
    out, _ = readable_c()
    t = cparse.parse(out.replace("struct g_20000030_t\n{\n    u32 f_0;\n    u16 f_4;\n    u8 pad_6[2];\n    u32 f_8;\n};",
                                 "").replace("struct g_20000044_t\n{\n    u32 f_0;\n    u16 f_4;\n};", ""))
    got = cmetrics.readable_figures(t, t.function("f"))
    assert (got["fixed_members"], got["fixed"], got["frame_members"], got["members"]) == (5, 2, 0, 0), got


def access_widths(target, obj):
    """{mnemonic: count} of the loads and stores of an object file's code (the cross objdump), but those
    of literals and of the stack (a register spilled or saved, which other register use changes)."""
    tool = "llvm-objdump" if target.compiler.kind == "clang" else target.compiler.command("objdump")[0]
    if not shutil.which(tool):
        return None
    text = subprocess.run([tool, "-d", obj], capture_output=True, text=True, check=True).stdout
    found = {}
    for m in re.finditer(r"^\s*[0-9a-f]+:\s+(?:[0-9a-f]{2,8}\s+)+(ldr\w*|str\w*|lw|lhu?|lbu?|sw|sh|sb)\b", text, re.M):
        rest = text[m.end():text.find("\n", m.end())]
        if "pc" in rest or "(sp)" in rest or "[sp" in rest:
            continue                        # (a literal's load: where the addresses come from; the stack)
        found[m.group(1)] = found.get(m.group(1), 0) + 1
    return found


@pytest.mark.compiles
def test_the_object_compiles_to_the_same_accesses(variant, tmp_path):
    """The readable C with structures of static data compiles on every gizmo build (GCC for ARMv4t and
    the Cortex-M3 and M4, clang for RV32), and where the build's objdump is at hand its loads and
    stores are those of the C with a macro per access: each one instruction of its width."""
    from conftest import gizmo_dir
    from dread import build
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    image = target.image()
    widths = []
    for label, plan in (("members", PLAN), ("macros", {})):
        src = tmp_path / label
        src.mkdir()
        (src / "f.c").write_text(readable_c(plan=plan)[0])
        objs = build.compile_all(target, image, str(src), str(tmp_path / (label + "-obj")), quiet=True,
                                 flags=target.compiler.lifted_cflags)
        assert len(objs) == 1
        widths.append(access_widths(target, objs[0]))
    if widths[0] is not None:
        assert widths[0] == widths[1] and sum(widths[0].values()) >= 7, widths


@needs_gcc
def test_the_pilot_names_a_structure_of_static_data_and_its_fields(tmp_path):
    """dread pilot's globals question, the model played, on gizmo-m3 with g_config as a structure of
    static data in its plan (put there by hand: gizmo is built without -fdata-sections, and its code
    alone does not tell the structure from a section anchor): the structure and each field it reaches
    are asked apart, and their names come into the readable C (config.volume)."""
    from conftest import GIZMO
    from dread import lift, llm, pilot, readable, regflow
    from dread.program import Program
    from dread.target import Target
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    target = Target.load(str(copy / "m3"))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    at = data(program)["g_config"]
    p = shapes.Plan()
    p.statics = {at: [(0, "u32"), (4, "u32"), (8, "u32")]}
    shapes.store_plan(image, p)
    context = readable.Context(target, image, program, flow)
    asked = []

    def answer(messages):
        asked.append(messages[0]["content"])
        return ('```json\n{"g_%x": "config", "g_%x.f_4": "volume", "g_%x.f_8": "mode", "g_%x.f_0": "config"}\n```'
                % (at, at, at, at))
    uses, by_function = pilot.global_uses(program, context, lifter, [f.name for f in program.functions])
    keys = sorted(by_function["print_status"])
    assert keys == ["0x%x struct" % at, "0x%x u32" % (at + 4), "0x%x u32" % (at + 8)], keys
    lines = []
    assert pilot.name_globals(target, image, program, llm.Scripted(answer), keys, uses, out=lines.append) == 3
    assert "g_%x (0x%x, struct, in " % (at, at) in asked[0] and "g_%x.f_4 (0x%x, u32, in " % (at, at + 4) in asked[0]
    out = readable.transform("print_status", lifter.lift(program.by_name["print_status"]).text,
                             context=readable.Context(target, image, program, flow))
    assert "mode_name(config.mode)" in out and "config.volume" in out, out
    assert "#define config (*(volatile struct config_t *)0x%xu)" % at in out, out
    assert "struct config_t\n{\n    u32 f_0;\n    u32 volume;\n    u32 mode;\n};" in out, out


# Arrays: a file of a structure array, a word array and a byte array, each reached with an index, and a
# function in another file that takes the address of one element
HERON_A = HEAD + """struct heron_slot { u32 count; u32 head; u16 mark; u16 spare; u32 tail; };
struct heron_slot heron_slots[4];
u32 heron_tally[6];
u8 heron_marks[8];
u32 heron_misc;
void heron_link(struct heron_slot *s, u32 v);
void heron_note(u32 i, u32 v) { heron_slots[i].count++; heron_slots[i].tail = v; heron_tally[i] += v; }
u32 heron_peek(u32 i) { return heron_slots[i].head + heron_slots[i].mark + heron_marks[i]; }
void heron_flag(u32 i) { heron_marks[i] = 1; heron_misc++; }
u32 heron_first(void) { return heron_slots[0].count + heron_slots[1].head + heron_tally[1] + heron_misc; }
void heron_hand(u32 i) { heron_link(&heron_slots[i], i); heron_link(&heron_slots[2], 0); }
"""
HERON_B = HEAD + """struct heron_slot { u32 count; u32 head; u16 mark; u16 spare; u32 tail; };
void heron_link(struct heron_slot *s, u32 v) { s->head = v; }
int main(void) { return 0; }
"""


def heron_plan(tmp_path, symbols=False):
    """(program, the trees of its readable C, structures, arrays) of the heron program built with
    -fdata-sections and debug information."""
    from dread import lift, readable, regflow
    program, _ = built(tmp_path, {"heron_a.c": HERON_A, "heron_b.c": HERON_B}, ["-fdata-sections", "-g"])
    target, image = program.target, program.image
    flow = regflow.load(target, image, program)
    context = readable.Context(target, image, program, flow)
    trees = readable.analysis_trees(program, context, lift.make_lifter(program, flow),
                                    [fn.name for fn in program.functions], named=context.globals)
    found, arrays, _ = readable.static_plan(program, context, trees, flow, symbols=symbols)
    return program, trees, found, arrays


@needs_gcc
def test_arrays_from_the_indexed_accesses_of_readable_c(tmp_path):
    """The indexed accesses of the readable C give each array its element size and its elements'
    fields; the code alone (no data symbols) starts each at the variable, and the debug information
    agrees: every array inside one variable, its element size the source's, every field a leaf of it.
    heron_slots counts two elements: the code reaches its second at a constant place."""
    program, trees, found, arrays = heron_plan(tmp_path)
    at = data(program)
    assert arrays == {at["heron_slots"]: (16, 2, [(0, "u32"), (4, "u32"), (8, "u16"), (12, "u32")]),
                      at["heron_tally"]: (4, 2, [(0, "u32")]), at["heron_marks"]: (1, 1, [(0, "u8")])}, arrays
    assert found == {}, found
    described = statics.describe(found, arrays)
    assert "0x%08x   32 bytes: 2 x 16: 0:u32, 4:u32, 8:u16, c:u32" % at["heron_slots"] in described, described
    got = statics.score(found, str(tmp_path / "t.elf"), arrays=arrays)
    if got["rows"]:                         # (without pyelftools: nothing to score against)
        assert (got["arrays"], got["arrays_one"], got["arrays_size"]) == (
            3, {"array of scalar": 2, "array of struct": 1}, 3), got
        assert got["array_fields"] == got["array_right"] == 11, got
    indexed = {fn.name: statics.indexed_accesses(fn, program.target.memory) for _, fn, _ in trees}
    assert (at["heron_slots"] + 12, 16, "u32", at["heron_slots"]) in indexed["heron_note"], indexed


def test_a_run_of_one_type_reached_with_an_index_is_an_array():
    """Without readable C (a function that does not lift) the machine code's indexed access at a
    structure whose fields are one type one after another makes it an array of that type; a structure
    whose fields differ stays one."""
    class Program:
        class target:
            memory = None
        class image:
            @staticmethod
            def startup():
                return []

        @staticmethod
        def symbols():
            return []
    ev = statics.Evidence("f")
    ev.accesses = [(0x20000100, "u32", 0x20000100, False), (0x20000104, "u32", 0x20000100, False),
                   (0x20000108, "u32", 0x20000100, False), (0x20000100, "u32", 0x20000100, True),
                   (0x20000140, "u32", 0x20000140, False), (0x20000144, "u16", 0x20000140, False),
                   (0x20000140, "u32", 0x20000140, True)]
    ev2 = statics.Evidence("g")             # (the neighbours through bases of their own)
    ev2.accesses = [(0x200000fc, "u32", 0x200000fc, False), (0x2000013c, "u32", 0x2000013c, False)]
    ev.accesses += ev2.accesses
    arrays = {}
    found = statics.plan(Program, {"f": ev}, indexed={}, arrays=arrays)
    assert arrays == {0x20000100: (4, 3, [(0, "u32")])}, arrays
    assert found == {0x20000140: [(0, "u32"), (4, "u16")]}, found


def test_a_double_word_access_is_two_fields_of_an_element():
    """An indexed 64-bit access (both words of an element stored at once) gives the element two word
    fields, not one double word over the fields the other accesses read."""
    ev = statics.Evidence("f")
    ev.accesses = [(0x20000100, "u32", 0x20000100, True), (0x20000104, "u32", 0x20000100, True)]
    indexed = {"f": [(0x20000100, 8, "u64"), (0x20000100, 8, "u32"), (0x20000104, 8, "u32")]}
    got = statics.find_arrays(indexed, {"f": ev}, {})
    assert got == {0x20000100: (8, 1, [(0, "u32"), (4, "u32")])}, got


def parse_expr(text):
    return cparse.parse("u32 f(void) { return %s; }" % text).function("f").body.body[0].e


def test_linear_forms_and_scaled_indices():
    """Sums, products with a constant and left shifts become a constant and terms times coefficients
    modulo 2**32; a narrowing cast is a term of its own, a wide one is looked through."""
    const, terms = statics.linear(parse_expr("0x200299f8u + ((((r2 << 2) + r2) << 2) + 4)"))
    assert const == 0x200299fc and {k: v[0] for k, v in terms.items()} == {"r2": 20}
    assert statics.linear(parse_expr("(u32)r3 - (r3 << 1) + 0x10u"))[1]["r3"][0] == 0xffffffff
    assert list(statics.linear(parse_expr("0x20u + (u8)r3 * 4"))[1]) == ["(u8)r3"]
    assert statics.constant(parse_expr("(0x10u << 2) - 4")) == 60 and statics.constant(parse_expr("r0 + 1")) is None
    c, k, index = statics.scaled(parse_expr("0x200299f8u + 0x14u * r9 + 4"))
    assert (c, k, cparse.expr_text(index)) == (0x200299fc, 20, "r9")
    assert statics.scaled(parse_expr("0x20000000u + r1 + r2")) is None          # (two terms)
    assert statics.scaled(parse_expr("0x20000000u - (r1 << 2)")) is None        # (a scale below 0)
    facts, used = {"r3": (20, cparse.Name("r8"), 0)}, set()
    c, k, index = statics.scaled(parse_expr("0x200299f8u + r3"), facts, used)
    assert (c, k, index.id, used) == (0x200299f8, 20, "r8", {"r3"})


FACTS = ("u32 f(u32 r0, u32 r1)\n{\n    u32 r2, r3, r4;\n"
         "    r3 = r0 << 2;\n"
         "    r2 = r3;\n"
         "    if (r1 != 0) r0 = r1;\n"
         "    r4 = r3 + 1;\n"
         "    r3 = 0x14u * r1;\n"
         "    r4 = r3 + 3;\n"
         "    while (r1 != 0) { r2 = r3 + 2; r1 = r1 - 1; }\n"
         "    r3 = (u32)*(volatile s16 *)0x20000100u;\n"
         "    return r3;\n}\n")


def test_what_a_variable_holds_as_an_index_times_a_scale():
    """straight_facts: r3 = r0 << 2 holds up to the statement that writes r0 (the if) and not in it;
    r3 = 0x14u * r1 holds before the loop but not in it (the loop writes r1); a load from memory or a
    copy is no index. scales_of: r3's assignments give it different scales, so it has none for the
    plan; r4 has none either (a copy of what r3 is there)."""
    fn = cparse.parse(FACTS).function("f")
    seen = []
    statics.straight_facts(fn, lambda s, facts: seen.append((cparse.unparse(s, notes=False).splitlines()[0].strip(),
                                                              {v: (k, x.id) for v, (k, x, *_) in facts.items()})))
    got = dict(seen)
    assert len(got) == len(seen), seen
    assert got["r2 = r3;"] == {"r3": (4, "r0")} and got["if (r1 != 0) r0 = r1;"] == {}, got
    assert got["r4 = r3 + 1;"] == {} and got["r4 = r3 + 3;"] == {"r3": (20, "r1")}, got
    assert got["while (r1 != 0)"] == got["r2 = r3 + 2;"] == got["return r3;"] == {}, got
    assert "r3" not in statics.scales_of(fn)
    one = cparse.parse("u32 g(u32 r0)\n{\n    u32 r3, r5;\n    r3 = r0 << 3;\n    r5 = r3 + 0x20000100u;\n"
                       "    return *(volatile u32 *)(r5 + 4);\n}\n").function("g")
    assert statics.scales_of(one) == {"r3": (8, None, 0, None), "r5": (8, None, 0x20000100, 0x20000100)}
    assert statics.indexed_accesses(one) == [(0x20000104, 8, "u32", 0x20000100)]


INDEXED = ("/* f at 0x0 */\n#include \"dread.h\"\nextern u32 heron_link(u32, u32);\n"
           "u32 f(u32 r0, u32 r1)\n{\n    u32 r2, r3, r4;\n"
           "    r3 = 0x14u * r0;\n"
           "    r2 = *(volatile u32 *)(0x20000100u + r3);\n"
           "    *(volatile u32 *)(0x20000104u + r3) = r2 + 1;\n"
           "    r4 = *(volatile u32 *)(0x20000100u + (((r1 << 2) + r1) << 2) + 12);\n"
           "    heron_link(0x20000100u + r3, 0x20000114u);\n"
           "    *(volatile u32 *)(0x20000200u + (r1 << 2)) = *(volatile u32 *)0x20000204u;\n"
           "    if (*(volatile u32 *)(0x20000110u + r3) == 0x2000010cu) r4 = 0;\n"
           "    *(volatile u8 *)(0x20000104u + r3) = 1;\n"
           "    r4 = r4 + (u32)*(volatile s32 *)(0x20000200u + (r0 << 2));\n"
           "    if (*(volatile u32 *)0x20000300u != 0x20000304u) heron_link(0x20000300u, 0x20000306u);\n"
           "    return r4;\n}\n"
           "u32 g(u32 r0)\n{\n    u32 r3;\n"
           "    r3 = r0 << 2;\n"
           "    *(volatile u32 *)(0x20000200u + r3) = 0x0u;\n"
           "    return *(volatile u32 *)(0x20000200u + r3);\n}\n")
ARRAYS = {0x20000100: (20, 2, [(0, "u32"), (4, "u32"), (12, "u32")]), 0x20000200: (4, 3, [(0, "u32")])}
STRUCTS = {0x20000300: [(0, "u32"), (4, "u16")]}


def indexed_c(arrays=ARRAYS, statics_=STRUCTS):
    t = cparse.parse(INDEXED)
    for name in ("f", "g"):
        naming.apply_globals(t, t.function(name), None, None, statics=statics_, arrays=arrays)
    return cparse.unparse(t, notes=False)


def test_the_elements_of_arrays_of_static_data_are_written_as_such():
    """An access at an array's constant plus its element size times an index is that element's field
    (directly or through a variable that holds the index times the size), at a constant address the
    element's; the addresses of elements, fields and objects that the code passes, assigns or
    compares are &-expressions; an access of the field's width but the other signedness is a cast when
    it is read; a byte inside a word field, a place at no field and a byte address inside a field stay
    as they were. The variable that held the index times the size goes where nothing reads it any more."""
    out = indexed_c()
    for line in ("r2 = g_20000100[r0].f_0;", "g_20000100[r0].f_4 = r2 + 1;", "r4 = g_20000100[r1].f_c;",
                 "heron_link((u32)&g_20000100[r0], (u32)&g_20000100[1]);", "g_20000200[r1] = g_20000200[1];",
                 "if (*(volatile u32 *)(0x20000110u + r3) == (u32)&g_20000100[0].f_c) r4 = 0;",
                 "*(volatile u8 *)(0x20000104u + r3) = 1;", "r4 = r4 + (u32)(s32)g_20000200[r0];",
                 "if (g_20000300.f_0 != (u32)&g_20000300.f_4) heron_link((u32)&g_20000300, 0x20000306u);",
                 "g_20000200[r0] = 0x0u;", "return g_20000200[r0];",
                 "#define g_20000100 ((volatile struct g_20000100_t *)0x20000100u)",
                 "#define g_20000200 ((volatile u32 *)0x20000200u)",
                 "struct g_20000100_t\n{\n    u32 f_0;\n    u32 f_4;\n    u8 pad_8[4];\n    u32 f_c;\n"
                 "    u8 pad_10[4];\n};"):
        assert line in out, (line, out)
    assert "r3 = 0x14u * r0;" in out and "r3 = r0 << 2;" not in out, out     # (f reads r3 still, g not)
    assert "u32 r3;" in out, out
    plain = indexed_c(arrays={}, statics_={})
    assert "struct" not in plain and "g_20000200" not in plain and "&" not in plain, plain


@pytest.mark.compiles
def test_elements_and_addresses_compile_to_the_same_accesses(variant, tmp_path):
    """The readable C with arrays and addresses of static data compiles on every gizmo build, and its
    loads and stores are those of the C without them: each one instruction of its width."""
    from conftest import gizmo_dir
    from dread import build
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    image = target.image()
    widths = []
    for label, text in (("arrays", indexed_c()), ("raw", indexed_c(arrays={}, statics_={}))):
        src = tmp_path / label
        src.mkdir()
        (src / "f.c").write_text(text)
        objs = build.compile_all(target, image, str(src), str(tmp_path / (label + "-obj")), quiet=True,
                                 flags=target.compiler.lifted_cflags)
        assert len(objs) == 1
        widths.append(access_widths(target, objs[0]))
    if widths[0] is not None:
        assert widths[0] == widths[1] and sum(widths[0].values()) == 11, widths


def test_an_index_into_static_data_is_no_pointer_for_the_shapes():
    """r3 + 0x200299fcu, where 0x200299fc lies in an array of static data, adds an index to the array
    (r3 is twenty times the priority): the shapes make no structure behind r3 of it (it had made one
    with a field 512 MB in)."""
    text = ("u32 f(u32 r0)\n{\n    u32 r3, r2;\n    r3 = 0x14u * r0;\n"
            "    r2 = *(volatile u32 *)(r3 + 0x200299fcu);\n    *(volatile u32 *)(r3 + 0x200299f8u) = r2;\n"
            "    return *(volatile u32 *)(r2 + 8) + *(volatile u32 *)(r2 + 4);\n}\n")
    t = cparse.parse(text)
    trees = [(t, t.function("f"), {})]
    before = shapes.structures(shapes.analyse(trees))
    assert any(off == 0x200299fc for rows in before.values() for off, _, _ in rows), before
    after = shapes.structures(shapes.analyse(trees, static_spans=[(0x200299f8, 0x20029a0c)]))
    assert not any(off >= 0x1000 for rows in after.values() for off, _, _ in rows), after
    assert any([o for o, _, _ in rows] == [4, 8] for rows in after.values()), after    # (what r2 points to)


def test_the_figures_count_elements_and_addresses_of_static_data():
    out = indexed_c()
    t = cparse.parse(re.sub(r"struct g_\w+_t\n\{.*?\n\};\n", "", out, flags=re.S))
    got = cmetrics.readable_figures(t, t.function("f"))
    assert (got["fixed_members"], got["fixed_indexed"], got["fixed_addresses"]) == (7, 5, 5), got
    assert got["fixed"] == 0, got


def test_a_plan_keeps_its_arrays_of_static_data():
    p = shapes.Plan()
    p.arrays = {0x200299f8: (20, 1, [(0, "u32"), (4, "u32")])}
    data_ = p.to_json()
    assert data_["arrays"] == {"0x200299f8": {"size": 20, "count": 1, "fields": [[0, "u32"], [4, "u32"]]}}
    assert shapes.Plan.from_json(data_).arrays == p.arrays
    assert "arrays" not in shapes.Plan().to_json() and shapes.Plan.from_json({"structs": {}}).arrays == {}


def test_an_array_and_its_elements_fields_take_the_table_s_names():
    """An array is named under its address ("0x... struct") with its elements' fields by offset; a field
    without a name there takes the name of its first element's address and type; the readable C reads
    egret_slots[r0].egret_head, and apply_globals reports the array and its elements' fields under the
    keys the pilot asks for."""
    named = {"0x20000100 struct": {"name": "egret_slots", "fields": {"4": "egret_head"}},
             "0x2000010c u32": {"name": "egret_tail"}, "0x20000200 struct": {"name": "egret_tally"}}
    t = cparse.parse(INDEXED)
    chosen = naming.apply_globals(t, t.function("f"), named, None, statics=STRUCTS, arrays=ARRAYS)
    out = cparse.unparse(t, notes=False)
    for line in ("r2 = egret_slots[r0].f_0;", "egret_slots[r0].egret_head = r2 + 1;",
                 "r4 = egret_slots[r1].egret_tail;", "egret_tally[r1] = egret_tally[1];",
                 "heron_link((u32)&egret_slots[r0], (u32)&egret_slots[1]);",
                 "#define egret_slots ((volatile struct egret_slots_t *)0x20000100u)"):
        assert line in out, (line, out)
    assert chosen["0x20000100 struct"] == "egret_slots", chosen
    assert chosen["0x20000104 u32"] == "egret_slots[].egret_head", chosen
    assert chosen["0x20000200 struct"] == "egret_tally" and chosen["0x20000300 u32"] == "g_20000300.f_0", chosen


@needs_gcc
def test_the_pilot_names_an_array_of_static_data_and_its_elements_fields(tmp_path):
    """dread pilot's globals question, the model played, on the heron program's arrays: the array and
    each field of its elements are asked apart (g_x and g_x[].f_4), with the lines that use them, and
    their names come into the readable C (egret_table[r0].egret_tail)."""
    from dread import lift, llm, pilot, readable, regflow, shapes
    program, trees, found, arrays = heron_plan(tmp_path)
    target, image = program.target, program.image
    at = data(program)["heron_slots"]
    p = shapes.Plan()
    p.statics, p.arrays = found, arrays
    shapes.store_plan(image, p)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = readable.Context(target, image, program, flow)
    asked = []

    def answer(messages):
        asked.append(messages[0]["content"])
        return ('```json\n{"g_%x": "egret_table", "g_%x[].f_c": "egret_tail", "g_%x[].f_8": "egret_mark"}\n```'
                % (at, at, at))
    uses, by_function = pilot.global_uses(program, context, lifter, [f.name for f in program.functions])
    keys = sorted(by_function["heron_note"])
    assert "0x%x struct" % at in keys and "0x%x u32" % (at + 12) in keys, keys
    assert pilot.name_globals(target, image, program, llm.Scripted(answer), keys, uses, out=lambda line: None) >= 2
    assert "g_%x[].f_c (0x%x, u32, in " % (at, at + 12) in asked[0], asked[0]
    assert "g_%x[r0].f_c = r1;" % at in asked[0], asked[0]
    out = readable.transform("heron_note", lifter.lift(program.by_name["heron_note"]).text,
                             context=readable.Context(target, image, program, flow))
    assert "egret_table[r0].egret_tail = r1;" in out, out


# ---- array bases where the code indexes them, and no address for a plain value (2026-10-23)

# An array of structures that the code indexes by id - 1, whose first field nothing reads, behind another
# array: GCC and clang make slot_ledgers' own address and fold the -1 into the index's arithmetic or into
# the access's offset, so that the constant part of every access lies an element below the array (in
# slot_floor, a data symbol in front of it)
SLOT_LEDGER = HEAD + """struct slot_ledger { u32 owner; u32 level; u16 mark; u16 spare; u32 limit; u32 seen; };
u32 slot_floor[8];
struct slot_ledger slot_ledgers[6];
u32 slot_tally;
u32 slot_floor_get(u32 i) { return slot_floor[i]; }
u32 slot_ledger_read(u32 id) { return slot_ledgers[id - 1].level; }
void slot_ledger_write(u32 id, u32 v)
{ slot_ledgers[id - 1].level = v; slot_ledgers[id - 1].limit = v + 1; slot_tally++; }
u32 slot_ledger_mark(u32 id) { return slot_ledgers[id - 1].mark + slot_ledgers[id - 1].seen; }
int main(void) { return 0; }
"""

KIND_TOML = {
    "a32": ('''[target]
name = "statics-a32"
[arch]
name = "arm"
isa = "a32"
profile = "a"
[memory]
regions = [{name = "flash", base = 0x0, size = 0x100000, kind = "rom"},
           {name = "ram", base = %(ram)s, size = 0x8000},
           {name = "scratch", base = 0x40100000, size = 0x20000}]
stack = {top = %(top)s, size = 0x1000}
sentinel = 0x0fff0000
scratch = {base = 0x40100000, size = 0x20000}
[image.main]
original = "original/t.bin"
base = 0x20000
limit = 0x30000
[compiler]
cross = "arm-none-eabi-"
cflags = ["-march=armv4t", "-marm", "-mabi=aapcs", "-mfloat-abi=soft", "-Os", "-ffreestanding"]
''', ["arm-none-eabi-gcc", "-march=armv4t", "-marm", "-mabi=aapcs", "-mfloat-abi=soft"], 0x20000, 0x40000000),
    "m3": ('''[target]
name = "statics-m3"
[arch]
name = "arm"
isa = "t32"
profile = "m"
cpu = "cortex-m3"
[memory]
regions = [{name = "flash", base = 0x08000000, size = 0x10000, kind = "rom"},
           {name = "ram", base = %(ram)s, size = 0x8000},
           {name = "scratch", base = 0x20100000, size = 0x20000}]
stack = {top = %(top)s, size = 0x1000}
sentinel = 0x1fff0000
scratch = {base = 0x20100000, size = 0x20000}
[image.main]
original = "original/t.bin"
base = 0x08000000
limit = 0x08010000
[compiler]
cross = "arm-none-eabi-"
cflags = ["-mthumb", "-mcpu=cortex-m3", "-Os"]
''', ["arm-none-eabi-gcc", "-mthumb", "-mcpu=cortex-m3"], 0x08000000, 0x20000000),
    "rv32": ('''[target]
name = "statics-rv32"
[arch]
name = "riscv"
isa = "rv32"
extensions = "imac"
[memory]
regions = [{name = "flash", base = 0x20000000, size = 0x100000, kind = "rom"},
           {name = "ram", base = %(ram)s, size = 0x8000},
           {name = "scratch", base = 0x80100000, size = 0x20000}]
stack = {top = %(top)s, size = 0x1000}
sentinel = 0x1fff0000
scratch = {base = 0x80100000, size = 0x20000}
[image.main]
original = "original/t.bin"
base = 0x20000000
limit = 0x20020000
[compiler]
cross = "riscv64-elf-"
cflags = ["-march=rv32imac_zicsr", "-mabi=ilp32", "-Os"]
''', None, 0x20000000, 0x80000000),
}


def kind_target(tmp_path, kind, source, ram=None):
    """The target of a C program (source) built with GCC -Os for kind ("a32": ARMv4T in ARM state,
    "m3": a Cortex-M3, "rv32": RV32IMAC), its code in flash and its data in RAM at ram (default the
    kind's) in the order of their names, imported from its ELF with its startup's ranges; its lift check
    run."""
    from conftest import RISCV_CROSS, RISCV_MARCH, riscv_toml
    from dread import elfimport, lift
    from dread.target import Target
    toml, cc, text, data = KIND_TOML[kind]
    ram = data if ram is None else ram
    if cc is None:
        cc = [RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32"]
        toml = riscv_toml(toml)
    (tmp_path / "t.c").write_text(source)
    (tmp_path / "t.ld").write_text("SECTIONS { . = 0x%x; .text : { *(.text*) *(.rodata*) } . = 0x%x; .data : "
                                   "{ *(SORT_BY_NAME(.data*)) } .bss : { *(SORT_BY_NAME(.bss*)) *(COMMON) } }\n"
                                   % (text, ram))
    elf = str(tmp_path / "t.elf")
    subprocess.run(cc + ["-Os", "-ffreestanding", "-nostdlib", "-fno-common", "-ffunction-sections", "-fdata-sections",
                         "-g", "-T", str(tmp_path / "t.ld"), "-Wl,-e,main", "-o", elf, str(tmp_path / "t.c")],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(toml % {"ram": hex(ram), "top": hex(ram + 0x8000)})
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    assert lift.main(["--target", str(tmp_path), "--check"]) == 0
    return Target.load(str(tmp_path))


def kind_plan(target, symbols=True):
    """(program, context, lifter, structures, arrays, shapes) of a target's static data as dread readable
    --check makes them (the lifted functions' readable C and every function's machine code)."""
    from dread import lift, readable, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    context = readable.Context(target, image, program, flow)
    lifter = lift.make_lifter(program, flow)
    trees = readable.analysis_trees(program, context, lifter, [fn.name for fn in program.functions],
                                    named=context.globals)
    found, arrays, s = readable.static_plan(program, context, trees, flow, symbols=symbols)
    return program, context, lifter, found, arrays, s


def needs_kind(kind):
    from conftest import needs_riscv_gcc
    return needs_riscv_gcc if kind == "rv32" else needs_gcc


@pytest.mark.parametrize("kind", [pytest.param(k, marks=needs_kind(k)) for k in ("a32", "m3", "rv32")])
def test_an_array_indexed_below_its_start_keeps_the_base_the_code_makes(kind, tmp_path):
    """slot_ledgers[id - 1].level: GCC -Os for ARM state, Thumb-2 and RISC-V makes slot_ledgers' own
    address (a literal, movw and movt, lui with addi) and folds the -1 into the index's arithmetic or
    the access's offset, so that readable C's constant part lies an element below the array. The
    array starts at the base the code makes, with or without the data symbols (before, it started at
    that constant, its structure at the lowest field read and every printed offset shifted, or, with
    the symbols, it was not found at all: slot_ledgers' symbol cut it to less than an element), the
    elements' structure has the unread first field's place as a gap, and readable C reads
    slot_ledgers[id - 1].level as g_x[r0 - 1].f_4; the gate accepts every function so."""
    from dread import build, readable
    from dread.program import Program
    target = kind_target(tmp_path, kind, SLOT_LEDGER)
    at = data(Program(target, target.image()))
    rows = [(4, "u32"), (8, "u16"), (12, "u32"), (16, "u32")]
    for symbols in (True, False):
        program, context, lifter, found, arrays, _ = kind_plan(target, symbols)
        assert arrays.get(at["slot_ledgers"]) == (20, 1, rows), (symbols, statics.describe(found, arrays))
        assert not any(at["slot_floor"] < a < at["slot_ledgers"] for a in list(arrays) + list(found)), arrays
    image = target.image()
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert accepted == sorted(build.read_names(image.table("lifted"))) and "slot_ledger_mark" in accepted, lines
    context = readable.Context(target, image)
    reg = "a0" if kind == "rv32" else "r0"
    name = "g_%x" % at["slot_ledgers"]
    for fn, wanted in (("slot_ledger_read", ["%s[%s - 1].f_4" % (name, reg)]),
                       ("slot_ledger_write", ["%s[%s - 1].f_4 = " % (name, reg), "%s[%s - 1].f_c = " % (name, reg)]),
                       ("slot_ledger_mark", ["%s[%s - 1].f_8" % (name, reg), "%s[%s - 1].f_10" % (name, reg)])):
        out = readable.transform(fn, lifter.lift(program.by_name[fn]).text, context=context)
        for text in wanted:
            assert text in out, (text, out)
        assert "struct %s_t\n{\n    u8 pad_0[4];\n    u32 f_4;\n    u16 f_8;" % name in out, out


def test_an_index_below_the_base_names_the_element_it_lies_in():
    """dread.statics base_of finds the literal a sum adds whole (also through a variable that holds it,
    straight_facts' and scales_of's facts), and an access whose constant lies below an array that starts
    at that base is that array's element index - 1 (not the element of the object in front that the
    constant lies in: a structure array before it, of the same element size)."""
    assert statics.base_of(parse_expr("0x14u * (r0 - 1) + 0x20000020u + 4")) == 0x20000020
    assert statics.base_of(parse_expr("(r0 + 0xffffffffu) * 0x14u + 0x80000020u + 0x4u")) == 0x80000020
    assert statics.base_of(parse_expr("r3 + 4"), {"r3": (20, cparse.Name("r0"), 0x2000000c, 0x20000020)}) == 0x20000020
    assert statics.base_of(parse_expr("0x20000020u - r3 + 0x20000000u")) is None       # (two of them)
    text = ("u32 f(u32 r0)\n{\n    u32 r3;\n    r3 = 0x14u * (r0 - 1) + 0x20000020u;\n"
            "    *(volatile u32 *)(r3 + 4) = 0x0u;\n"
            "    return *(volatile u32 *)(0x20000020u + ((r0 + (r0 << 2)) << 2) - 16);\n}\n")
    t = cparse.parse(text)
    fn = t.function("f")
    assert statics.indexed_accesses(fn) == [(0x20000010, 20, "u32", 0x20000020)] * 2
    arrays = {0x20000000: (20, 1, [(0, "u32"), (4, "u32"), (16, "u32")]), 0x20000020: (20, 2, [(4, "u32")])}
    naming.apply_globals(t, fn, None, None, statics={}, arrays=arrays)
    out = cparse.unparse(t, notes=False)
    assert "g_20000020[r0 - 1].f_4 = 0x0u;" in out and "return g_20000020[r0 - 1].f_4;" in out, out
    assert "g_20000000" not in out, out


def test_where_an_array_starts_from_the_addresses_the_code_made():
    """find_arrays: accesses whose constants lie below every address the code made them from start the
    array at the lowest of those (table[i - 1]), or at a data symbol within an element below it;
    where one function made the array's start and another a field's address in it (FreeRTOS GCC -Os
    loads pxReadyTasksLists + 4 in one function, pxReadyTasksLists in others), the start stays where it
    was (the lowest made address at or below the accesses); an anchor far below is no start; a table
    without a symbol indexed by c - 'b' starts at its own address, not at the variable in front of it."""
    table, field = 0x20000120, 0x20000104          # (an array at 0x20000120; one at 0x20000100, its field 4)

    def start(rows, symbols=()):
        got = statics.find_arrays({"f": rows}, {}, {}, data_symbols=symbols)
        return sorted((a, k, n) for a, (k, n, _) in got.items())
    below = [(table - 16, 20, "u32", table), (table - 8, 20, "u32", table)]
    assert start(below) == [(table, 20, 1)]
    assert start(below, {table - 20}) == [(table, 20, 1)]       # (a symbol in front: another variable)
    assert start(below, {table - 20, table}) == [(table, 20, 1)]
    both = [(0x20000100, 20, "u32", 0x20000100), (field - 4, 20, "u32", field), (field + 8, 20, "u32", field)]
    assert start(both) == [(0x20000100, 20, 1)]
    only = both[1:]                                 # (no function made the array's start)
    assert start(only, {0x20000100}) == [(0x20000100, 20, 1)]
    assert start(only) == [(field, 20, 1)]          # (without symbols: not established, the field's address)
    anchor = [(0x20000100 + 52, 8, "u32", 0x20000100)]
    assert start(anchor) == [(0x20000100 + 52, 8, 1)]
    switch = [(0x20000200 - 0x62, 1, "s8", 0x20000200)]    # (a table without a symbol indexed by c - 'b')
    assert start(switch, {0x20000200 - 0x10}) == [(0x20000200, 1, 1)]     # (not the variable in front)


NOTES = ("/* f at 0x0 */\n#include \"dread.h\"\nextern u32 gauge_note(u32, u32);\n"
         "u32 f(u32 r0, u32 r1)\n{\n    u32 r3, r4, r5, r6, r7;\n"
         "    r6 = *(volatile u32 *)(r0 + 12);\n"
         "    if (r6 != 0x404u) r0 = r0 + 4;\n"
         "    r7 = *(volatile u32 *)(r0 + 16);\n"
         "    if (r7 != 0x408u) r1 = r1 + 4;\n"
         "    r7 = (u32)*(volatile u16 *)(r0 + 2);\n"
         "    if (r7 != 0x408u) r1 = r1 + 8;\n"
         "    r3 = r0 & 0xf00u;\n"
         "    if (r3 == 0x400u) r1 = r1 + 1;\n"
         "    if ((r1 & 0x7f0u) != 0x404u) r0 = 0x0u;\n"
         "    if (r1 >> 4 == 0x400u) r0 = r0 + 2;\n"
         "    if (r1 == 0x404u) r0 = r0 + 3;\n"
         "    gauge_note(0x400u, 0x404u);\n"
         "    r4 = 0x400u;\n"
         "    r5 = 0x404u;\n"
         "    *(volatile u32 *)(r4 + 8) = r5 + r1;\n"
         "    if (*(volatile u32 *)(r0 + 4) == 0x408u) r0 = 1;\n"
         "    *(volatile u32 *)0x404u = r0;\n"
         "    return 0x408u;\n}\n")
NOTE_PLAN = {0x400: [(0, "u32"), (4, "u32"), (8, "u32")]}


def notes_c(pointers=None, image=None):
    t = cparse.parse(NOTES)
    naming.apply_globals(t, t.function("f"), None, None, statics=NOTE_PLAN, pointers=pointers, image=image)
    return cparse.unparse(t, notes=False)


def test_no_address_for_a_plain_value():
    """A static object at a small address (0x400) and values that happen to be its address: one compared
    with a masked word (directly, or a variable given the mask), with a shifted one, with a parameter
    used only as a number, assigned to a variable used only in arithmetic, passed where the callee takes
    a count (pointers: dread.shapes pointer_params) stay numbers; one assigned to a variable the
    function dereferences, compared with a word read from memory (directly, kept in a variable, or in a
    variable that holds it there and a halfword later), passed where the callee takes a pointer, stored
    and returned is the object's address; and none for an object where the image keeps no static data
    (image)."""
    out = notes_c()
    for line in ("if (r6 != (u32)&g_400.f_4) r0 = r0 + 4;", "if (r7 != (u32)&g_400.f_8) r1 = r1 + 4;",
                 "if (r7 != 0x408u) r1 = r1 + 8;", "if (r3 == 0x400u)", "if ((r1 & 0x7f0u) != 0x404u)",
                 "if (r1 >> 4 == 0x400u)",
                 "if (r1 == 0x404u)", "r5 = 0x404u;", "r4 = (u32)&g_400;", "gauge_note((u32)&g_400, (u32)&g_400.f_4);",
                 "if (*(volatile u32 *)(r0 + 4) == (u32)&g_400.f_8) r0 = 1;", "g_400.f_4 = r0;",
                 "return (u32)&g_400.f_8;"):
        assert line in out, (line, out)
    assert "gauge_note(0x400u, (u32)&g_400.f_4);" in notes_c({"gauge_note": [1]})
    assert "gauge_note(0x400u, 0x404u);" in notes_c({"gauge_note": []})
    outside = notes_c(image=[(0x20000000, 0x20001000)])
    assert "&g_" not in outside and "g_400.f_4 = r0;" in outside, outside
    assert "r4 = (u32)&g_400;" in notes_c(image=[(0x400, 0x40c)])


# A structure array at a small address (RAM from 0x400), a bit field's values compared with a masked and
# a shifted word that happen to be its address and its second field's, a count that is the address of a
# field handed to a function that counts with it, and real addresses: a field's handed to a function
# that stores through it, a cursor compared with one
GAUGE = HEAD + """struct gauge_cell { u32 level; u32 limit; u32 mark; };
struct gauge_cell gauge_cells[4];
u32 *gauge_cursor;
u32 gauge_state;
__attribute__((noinline)) void gauge_link(u32 *p, u32 v) { *p = v; }
__attribute__((noinline)) u32 gauge_spin(u32 n) { u32 s = 0; while (n--) s += n; return s; }
__attribute__((noinline)) u32 gauge_same(const u8 *a, const u8 *b, u32 n)
{ u32 i; for (i = 0; i < n; i++) if (a[i] != b[i]) return 1; return 0; }
u32 gauge_check(void) { return gauge_same((const u8 *)&gauge_cells[0].limit, (const u8 *)gauge_cursor, 4); }
u32 gauge_pick(u32 i) { return gauge_cells[i].limit + gauge_cells[i].mark; }
void gauge_mode(u32 w) { if ((w & 0xf00) == 0x400) gauge_state = 1; }
void gauge_band(u32 w) { if ((w >> 4) == 0x404) gauge_state = 2; }
void gauge_hook(u32 v) { gauge_link(&gauge_cells[0].mark, v); gauge_state = gauge_spin(0x404); }
void gauge_back(void) { if (gauge_cursor == &gauge_cells[0].limit) gauge_cursor = &gauge_cells[1].limit; }
int main(void) { return 0; }
"""


@needs_gcc
def test_a_bit_field_value_at_a_small_address_stays_a_number(tmp_path):
    """The gauge program on a Cortex-M3 with its RAM at 0x400: gauge_cells is an array at 0x400, and
    gauge_mode's masked compare with 0x400 (through a variable that holds the masked word), gauge_band's
    shifted one with 0x404, and gauge_spin's count 0x404 stay numbers (they had read (u32)&g_400[0] and
    (u32)&g_400[0].f_4), while the field's address handed to gauge_link, which stores through it, and
    to gauge_same, which reads bytes at it plus an index (a sum the shapes do not follow), and the one
    the cursor is compared with are addresses (since dread.vartypes, &g_400[0].f_8 where gauge_link's
    extern takes the pointer it stores through); the gate accepts every function."""
    from dread import build, readable
    target = kind_target(tmp_path, "m3", GAUGE, ram=0x400)
    image = target.image()
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert accepted == sorted(build.read_names(image.table("lifted"))) and "gauge_mode" in accepted, lines
    from dread import lift, regflow
    from dread.program import Program
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    context = readable.Context(target, image, program)
    assert context.plan.arrays.get(0x400, (None,))[0] == 12 and context.plan.params["gauge_link"] == [0], \
        (context.plan.arrays, context.plan.params)
    assert context.plan.params["gauge_spin"] == [] and {0, 1} <= set(context.plan.params["gauge_same"]), \
        context.plan.params             # (its count too where the loop adds it to a pointer: not told apart)

    def text(name):
        return readable.transform(name, lifter.lift(program.by_name[name]).text, context=context)
    mode, band, hook, back = text("gauge_mode"), text("gauge_band"), text("gauge_hook"), text("gauge_back")
    assert "== 0x400u)" in mode and "&g_" not in mode, mode
    assert "0x404u == r0 >> 4" in band and "&g_" not in band, band
    # (an address, as an integer or, where dread.vartypes types the extern's parameter, as a pointer)
    assert re.search(r"gauge_link\((\(u32\))?&g_400\[0\]\.f_8, r0\);", hook) and "gauge_spin(0x404u)" in hook, hook
    assert "== (u32)&g_400[0].f_4)" in back, back
    assert re.search(r"gauge_same\((\([\w *]+\))?&g_400\[0\]\.f_4, ", text("gauge_check")), text("gauge_check")


def test_the_arrays_of_gizmo_start_where_its_code_makes_their_address(variant):
    """On every gizmo build, each array of static data starts at a data symbol or at an address the
    machine code makes (dread.relations materialized: a literal, movw and movt, lui with addi): gizmo
    a32's handle_key indexes its switch table by c - 'b' from the table's literal, and the table started
    98 bytes in front of it, at the constant readable C's sum folded (a place the code never makes)."""
    from conftest import gizmo_dir
    from dread.program import Program
    from dread.relations import materialized
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    program = Program(target, target.image())
    made = set().union(*(materialized(program, fn) for fn in program.functions))
    symbols = set(data(program).values())
    _, _, _, found, arrays, _ = kind_plan(target)
    assert all(a in made or a in symbols for a in arrays), (sorted(arrays), statics.describe(found, arrays))
    if variant == "a32":                    # (handle_key's table: its literal, 0x20dac)
        assert arrays.get(0x20dac, (None,))[0] == 1 and 0x20dac - 98 not in arrays, statics.describe(found, arrays)
