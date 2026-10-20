"""C++ on gizmo-m3cpp (g++) and gizmo-rv32cpp (clang++), both with examples/gizmo/fw/shapes.cpp
(virtual methods, a template used twice, a static object with a constructor; -fno-exceptions
-fno-rtti): names demangled and grouped into template families (dread.cxx), .cpp sources in the
build, a method rewritten in C++ and one in C, a moved virtual method still reached through its
vtable, the constructor run by the startup in the system run, and cases for calls through a vtable
(generators 29 and 30)."""
import io
import json
import os
import re
import shutil
import struct
from contextlib import redirect_stdout

import pytest

from dread import build, cxx, mkasm, mutate, runner, sysrun
from dread.autocase import Generator
from dread.elf import Elf
from dread.program import Program
from dread.target import Target
from conftest import GIZMO, gizmo_dir, have

M3CPP = gizmo_dir("m3cpp")
REWRITTEN = ["_ZNK3Tri4areaEv", "_ZNK4Rect4areaEv", "_Z5boundIjET_S0_S0_S0_", "_Z5boundIiET_S0_S0_S0_"]

needs_filt = pytest.mark.skipif(cxx.demangler() is None, reason="needs c++filt or llvm-cxxfilt")
# the C++ builds and the tools that build and assemble each
BUILDS = {"m3cpp": ("arm-none-eabi-g++", "arm-none-eabi-as"), "rv32cpp": ("clang++", "ld.lld", "llvm-objcopy")}


def needs(variant):
    missing = [t for t in BUILDS[variant] if not have(t)]
    if missing or cxx.demangler() is None:
        pytest.skip("needs " + ", ".join(missing or ["c++filt"]))


def test_parts_of_demangled_names():
    assert cxx.parts("unsigned int bound<unsigned int>(unsigned int, unsigned int, unsigned int)") == \
        ("bound", "unsigned int", "unsigned int, unsigned int, unsigned int", "")
    assert cxx.parts("Tri::area() const") == ("Tri::area", None, "", "const")
    assert cxx.parts("measure(Shape const*)") == ("measure", None, "Shape const*", "")
    assert cxx.template_key("ns::Box<int>::get(int) const") == "ns::Box<...>::get"
    assert cxx.template_key("std::pair<int, Box<char> > make<int>(int)") == "make<...>"
    assert cxx.template_key("Tri::area() const") is None
    assert cxx.template_key("Foo::operator<(Foo const&) const") is None
    assert not cxx.is_mangled("clamp") and cxx.source_patterns("clamp") == []


@needs_filt
def test_names_demangled_and_template_families():
    shown = cxx.demangle(["_ZNK3Tri4areaEv", "_ZL7measurePK5Shape", "main"])
    assert shown == {"_ZNK3Tri4areaEv": "Tri::area() const", "_ZL7measurePK5Shape": "measure(Shape const*)",
                     "main": "main"}
    names = [fn.name for fn in Program(Target.load(M3CPP)).functions]
    assert cxx.families(names) == [("bound<...>", ["_Z5boundIiET_S0_S0_S0_", "_Z5boundIjET_S0_S0_S0_"])]
    assert cxx.families(["_ZNK2ns3BoxIiE3getEi", "_ZNK2ns3BoxIcE3getEc"]) == \
        [("ns::Box<...>::get", ["_ZNK2ns3BoxIcE3getEc", "_ZNK2ns3BoxIiE3getEi"])]


@needs_filt
def test_mutate_finds_cpp_definitions_by_their_source_names(tmp_path):
    src = tmp_path / "box.cpp"
    src.write_text("namespace ns {\ntemplate <typename T> class Box {\npublic:\n    T get(T v) const;\n"
                   "private:\n    T x_;\n};\n\ntemplate <typename T>\nT Box<T>::get(T v) const\n{\n    return x_ + v;\n}\n}\n")
    path, lines, start, end, static = mutate.find_function([str(src)], "_ZNK2ns3BoxIiE3getEi")
    assert lines[start:end] == ["    return x_ + v;"]
    paths = [os.path.join(M3CPP, "src", f) for f in ("shapes.cpp", "rect.c")]
    for name, body in (("_ZNK3Tri4areaEv", "return base_ * height_ / 2;"),
                       ("_Z5boundIiET_S0_S0_S0_", "return v < lo ? lo : v > hi ? hi : v;"),
                       ("_ZNK4Rect4areaEv", "return self->w * self->h;")):
        path, lines, start, end, static = mutate.find_function(paths, name)
        assert [l.strip() for l in lines[start:end]] == [body], name


@pytest.mark.parametrize("variant", sorted(BUILDS))
def test_listing_rebuilds_identically_with_names_shown(variant, tmp_path):
    needs(variant)
    out = tmp_path / "asm"
    rc = mkasm.main(["--target", gizmo_dir(variant), "--verify", "--out", str(out), "--build", str(tmp_path / "b"),
                     "--raw", str(tmp_path / "raw.txt")])
    assert rc == 0
    body = "".join(p.read_text() for p in out.glob("[0-9a-f]*.s"))
    comment = "#" if variant.startswith("rv32") else "@"
    assert comment + " ---- _ZNK3Tri4areaEv (Tri::area() const, 0x" in body and "\n_ZNK3Tri4areaEv:" in body


@pytest.fixture(scope="module", params=sorted(BUILDS))
def built(request, tmp_path_factory):
    """A copy of gizmo with the build's cached cases, rebuilt: its C and C++ sources and its lifted
    functions (config/lifted.txt), as dread recon builds it."""
    variant = request.param
    needs(variant)
    copy = tmp_path_factory.mktemp("cxx") / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir(variant), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, copy / variant / "build" / cache)
    target = Target.load(str(copy / variant))
    out = copy / variant / "build" / "recon" / "main"
    payload = build.build(target, target.image(), out=str(out), quiet=True)
    return variant, target, payload, out


def test_cpp_sources_build_and_their_functions_agree(built):
    variant, target, payload, out = built
    objects = {os.path.relpath(os.path.join(d, f), out / "obj") for d, _, fs in os.walk(out / "obj") for f in fs
               if f.endswith(".o")}
    assert {"shapes.cpp.o", "rect.o", "rewritten.o"} <= objects
    assert set(REWRITTEN) <= build.defined_functions([str(out / "obj" / o) for o in objects])
    elf = Elf(str(out / "obj" / "shapes.cpp.o"))
    assert not [s.name for s in elf.sections if s.name.startswith(".gcc_except")]
    # no vtable, typeinfo or guard of its own: the image keeps its vtables
    assert not [s.name for s in elf.symbols if s.shndx and s.name.startswith(("_ZTV", "_ZTI", "_ZTS", "_ZGV"))]
    runner.prepare(target, target.image(), payload)
    lines = []
    failed, uncovered, _ = runner.run(REWRITTEN, out=lines.append, jobs=2)
    text = "\n".join(lines)
    assert failed == 0 and not uncovered, text
    assert all(re.search(r"ok +%s +\d+/\d+ cases agree" % re.escape(n), text) for n in REWRITTEN), text


def test_the_system_run_agrees_through_vtables_and_the_constructor(built):
    """The system run calls the methods through the vtables in the image's data and agrees; the line
    "shapes 158" needs the static constructor that the startup runs from .init_array (116 without
    it). On m3cpp, Square::sides is lifted and larger than the original: it moves, the overlay
    leaves a branch at its entry, and Square's vtable, unchanged, still points there."""
    variant, target, payload, out = built
    if variant == "m3cpp":
        with open(out / "placement.json") as f:
            placed = json.load(f)["functions"]
        elf = Elf(os.path.join(gizmo_dir(variant), "original", "gizmo.elf"))
        vtable = next(s for s in elf.symbols if s.name == "_ZTV6Square")
        with open(os.path.join(gizmo_dir(variant), "original", "gizmo.bin"), "rb") as f:
            image = f.read()
        words = struct.unpack_from("<%dI" % (vtable.size // 4), image, vtable.value - target.image().base)
        entries = {(w & ~1) for w in words}
        moved = [n for n, p in placed.items() if p.get("branch") and p["entry"] in entries]
        assert "_ZNK6Square5sidesEv" in moved
    text = io.StringIO()
    with redirect_stdout(text):
        rc = sysrun.main(["--target", target.root, "--scenario", "boot", "--recon", payload])
    assert rc == 0, text.getvalue()
    assert "shapes 158" in text.getvalue() and "the images agree" in text.getvalue()


@pytest.mark.parametrize("variant", sorted(BUILDS))
def test_cases_for_calls_through_a_vtable(variant):
    """measure(const Shape *) calls two methods through the object's vtable: an input needs a
    pointer to an object, whose first word points to a table, whose words are code. Every input
    faulted before generator 29 (code pointers were learned only in a pass seeded by cases that
    ended, and none ended), and on RISC-V before generator 30 (the object's word was learned two
    bytes off first, and the aligned runs never read it whole)."""
    needs(variant)
    t = Target.load(gizmo_dir(variant))
    g = Generator(t, t.image())
    fn = g.program.by_name["_ZL7measurePK5Shape"]
    cases = g.generate(fn.name, fn.entry, fn.insns)
    got = set()
    for c in cases:
        got |= g.coverage(fn.entry, fn.insns, c, c.get("limit", 200_000))
    assert got == set(fn.insns)
    code = g.fake_code(fn.entry)
    assert any(v == code for c in cases for _, n, v in c["mem"] if n == 4)
