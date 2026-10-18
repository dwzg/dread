"""The target description: gizmo's two descriptions read as written, and mistakes refused."""
import os
import textwrap

import pytest

from dread.target import Target, TargetError
from conftest import gizmo_dir

MINIMAL = """
[target]
name = "t"
[arch]
name = "arm"
[image.main]
original = "image.bin"
base = 0x1000
"""


def write(tmp_path, text):
    (tmp_path / "dread.toml").write_text(textwrap.dedent(text))
    (tmp_path / "image.bin").write_bytes(b"\0" * 16)
    return str(tmp_path)


def test_gizmo_targets_load(target, variant):
    image = target.image()
    assert target.name == "gizmo-" + variant
    assert image.name == "main" and target.default_image == "main"
    assert len(image.payload()) > 1000
    assert image.limit > image.end
    assert [c.kind for c in image.startup()] == ["code", "data", "zero"]
    assert target.abi.arg_regs == ["r0", "r1", "r2", "r3"]
    assert target.memory.is_io(target.memory.io[0][0])
    assert target.memory.region(target.memory.stack_top - 4) is not None
    if variant == "m3":
        assert target.arch.profile == "m" and target.arch.isa == "t32"
        assert image.entry & 1 == 0 and image.base <= image.entry < image.end   # from the symbol
    else:
        assert target.arch.isa == "a32" and image.entry == image.base


def test_found_from_a_subdirectory(tmp_path):
    root = write(tmp_path, MINIMAL)
    sub = os.path.join(root, "src", "deep")
    os.makedirs(sub)
    assert Target.load(start=sub).root == root


def test_minimal_defaults(tmp_path):
    t = Target.load(write(tmp_path, MINIMAL))
    img = t.image()
    assert img.limit == img.end == 0x1010
    assert img.erased == 0xFF and img.startup() == []
    assert img.table("functions").endswith(os.path.join("config", "functions.tsv"))
    assert t.compiler.cross == "arm-none-eabi-"


@pytest.mark.parametrize("text, message", [
    (MINIMAL.replace('name = "t"', 'name = "t"\ntitel = "typo"'), "unknown key \\[target\\] titel"),
    (MINIMAL + "[mem]\nio = []\n", "unknown section \\[mem\\]"),
    (MINIMAL + "[memory]\nregions = [{base = 0, size = 0x100}, {base = 0x80, size = 0x100}]\n", "overlap"),
    (MINIMAL + "[image.main.tables]\nfunktions = 'f.tsv'\n", "unknown tables"),
    (MINIMAL.replace("base = 0x1000", "base = '0x1000'"), "should be int"),
    (MINIMAL.replace('[arch]\nname = "arm"', '[arch]\nname = "mips"'), "unknown architecture"),
])
def test_mistakes_are_refused(tmp_path, text, message):
    with pytest.raises(TargetError, match=message):
        Target.load(write(tmp_path, text))


def test_unknown_key_in_image(tmp_path):
    with pytest.raises(TargetError, match="unknown key"):
        Target.load(write(tmp_path, MINIMAL + "orginal = 'x'\n"))


def test_m_profile_needs_thumb(tmp_path):
    text = MINIMAL.replace('name = "arm"', 'name = "arm"\nprofile = "m"')
    with pytest.raises(TargetError, match="Thumb only"):
        Target.load(write(tmp_path, text))


def test_image_override_of_arch(tmp_path):
    text = MINIMAL + '[image.boot]\noriginal = "image.bin"\nbase = 0\narch = {isa = "t32"}\n'
    t = Target.load(write(tmp_path, text))
    assert t.image("boot").arch.isa == "t32" and t.image("main").arch.isa == "a32"
    with pytest.raises(TargetError, match="no image"):
        t.image("other")


def test_plugin_from_a_file(tmp_path):
    root = write(tmp_path, MINIMAL.replace("base = 0x1000", 'base = 0x1000\nstartup = {plugin = "startup.py:copies"}'))
    (tmp_path / "startup.py").write_text(textwrap.dedent("""
        from dread.target import Copy
        def copies(image):
            return [Copy("data", 0x2000, 8, image.base)]
    """))
    img = Target.load(root).image()
    assert img.startup()[0].run == 0x2000 and img.startup()[0].stored == 0x1000


def test_gizmo_dirs_exist():
    assert os.path.exists(os.path.join(gizmo_dir("a32"), "dread.toml"))


def test_gizmo_startup_lists_match_the_elf(target, variant):
    """make -C examples/gizmo/fw targets writes them (import-elf --write-startup); a stale list
    made the analysis read the wrong bytes for the RAM function once."""
    from dread.elf import Elf
    from dread.elfimport import startup
    elf = Elf(os.path.join(target.root, "original", "gizmo.elf"))
    assert [(c.kind, c.run, c.size, c.stored) for c in target.image().startup()] == startup(elf)


def test_write_startup_replaces_only_the_list(tmp_path):
    from dread.elfimport import write_startup
    path = tmp_path / "dread.toml"
    path.write_text('[target]\nname = "x"\n\n[image.main]\nbase = 0x0\nstartup = [\n'
                    '    {kind = "zero", run = 0x100, size = 0x10},\n]\n\n[memory]\n')
    copies = [("data", 0x200, 0x20, 0x40)]
    assert write_startup(str(path), "main", copies)
    text = path.read_text()
    assert '{kind = "data", run = 0x200, size = 0x20, stored = 0x40},' in text and "0x100" not in text
    assert text.startswith('[target]\nname = "x"') and text.rstrip().endswith("[memory]")
    assert not write_startup(str(path), "main", copies)          # unchanged the second time
