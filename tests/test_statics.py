"""dread.statics: structures of static data at fixed addresses, found from the machine code. What one
base reaches at constant offsets is a structure where the code reaches its neighbours through bases of
their own (GCC with -fdata-sections); GCC's section anchor, the one base a file reaches all of its
variables through (without -fdata-sections), is none, and an indexed table behind it is an array that
starts at its constant part; an address handed out and the target's data symbols end a structure.
Readable C writes a structure's fields as members of one object (g_20000030.f_4), every access one of
its width at its address."""
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
    """{mnemonic: count} of the loads and stores of an object file's code (the cross objdump)."""
    tool = "llvm-objdump" if target.compiler.kind == "clang" else target.compiler.command("objdump")[0]
    if not shutil.which(tool):
        return None
    text = subprocess.run([tool, "-d", obj], capture_output=True, text=True, check=True).stdout
    found = {}
    for m in re.finditer(r"^\s*[0-9a-f]+:\s+(?:[0-9a-f]{2,8}\s+)+(ldr\w*|str\w*|lw|lhu?|lbu?|sw|sh|sb)\b", text, re.M):
        if "pc" in text[m.end():text.find("\n", m.end())]:
            continue                        # (a literal's load: where the addresses come from)
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
