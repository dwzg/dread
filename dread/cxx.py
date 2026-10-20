"""C++ names: demangled for display, grouped into template families.

A C++ image keeps its mangled names everywhere DREAD works with names (the tables, the listing's
labels, the lifted and rewritten functions' symbols: the linker needs them so); people read them
demangled. demangle() asks c++filt (binutils', LLVM's llvm-cxxfilt, or a cross toolchain's), once
per batch and cached; without one, names stay as they are.

A template family is the instances of one template: of a function template (bound<int> and
bound<unsigned int>) or the same method of a class template (Box<int>::get and Box<char>::get):
the same source, compiled once per type. A rewrite written as the template covers every instance
at once (an explicit instantiation per instance in the .cpp file).
"""
import os
import shutil
import subprocess

DEMANGLERS = ("c++filt", "llvm-cxxfilt", "arm-none-eabi-c++filt")

_CACHE = {}


def is_mangled(name):
    """Whether a name is an Itanium C++ ABI mangled name (_Z...)."""
    return name.startswith("_Z") and len(name) > 2


def demangler():
    """The command that demangles names on its standard input, or None (DREAD_CXXFILT overrides)."""
    choice = os.environ.get("DREAD_CXXFILT")
    if choice:
        return choice
    return next((d for d in DEMANGLERS if shutil.which(d)), None)


def demangle(names):
    """{name: its demangled form} for the given names; a name that is not mangled, or that no
    demangler could read, maps to itself."""
    names = list(names)
    todo = sorted({n for n in names if is_mangled(n) and n not in _CACHE})
    tool = demangler() if todo else None
    if tool:
        try:
            out = subprocess.run([tool], input="\n".join(todo) + "\n", capture_output=True, text=True,
                                 timeout=60).stdout.split("\n")
        except (OSError, subprocess.SubprocessError):
            out = []
        if len(out) >= len(todo):
            _CACHE.update(zip(todo, out))
    return {n: _CACHE.get(n, n) for n in names}


def pretty(name):
    """One name demangled (itself when it is not mangled)."""
    return demangle([name])[name]


def parts(demangled):
    """(qualified name, template arguments or None, parameters, qualifiers) of a demangled function
    name, the return type left out: "unsigned int bound<unsigned int>(unsigned int, unsigned int)"
    gives ("bound", "unsigned int", "unsigned int, unsigned int", ""), "Tri::area() const" gives
    ("Tri::area", None, "", "const"). None when it does not look like a function."""
    # the parameter list: the last parenthesis group at the top level
    depth, close = 0, -1
    for i in range(len(demangled) - 1, -1, -1):
        ch = demangled[i]
        if ch in ")>":
            if depth == 0 and ch == ")":
                close = i
                break
            depth += 1
        elif ch in "(<":
            depth -= 1
    if close < 0:
        return None
    depth, open_ = 0, -1
    for i in range(close, -1, -1):
        ch = demangled[i]
        if ch in ")>":
            depth += 1
        elif ch in "(<":
            depth -= 1
            if depth == 0:
                open_ = i
                break
    if open_ <= 0:
        return None
    head, params, quals = demangled[:open_], demangled[open_ + 1:close], demangled[close + 1:].strip()
    targs = None
    if head.endswith(">"):
        depth = 0
        for i in range(len(head) - 1, -1, -1):
            if head[i] == ">":
                depth += 1
            elif head[i] == "<":
                depth -= 1
                if depth == 0:
                    targs, head = head[i + 1:-1], head[:i]
                    break
    # the return type ends at the last space outside brackets ("unsigned int bound")
    depth, cut = 0, 0
    for i, ch in enumerate(head):
        if ch in "(<":
            depth += 1
        elif ch in ")>":
            depth -= 1
        elif ch == " " and depth == 0:
            cut = i + 1
    return head[cut:], targs, params, quals


def source_patterns(name):
    """Regular expressions for the name a C++ definition carries in the source text, most specific
    first ([] for a name that is not mangled): Tri::area for _ZNK3Tri4areaEv, bound for an
    instance of the template bound (the definition every instance shares), Box<...>::get for a
    method of a class template (its arguments as the source names them: Box<T>::get), and that
    without its leading namespaces down to Class::method (a definition inside namespace ns { }).
    A function inside a namespace is looked for qualified only (ns::f, not f: a bare name could be
    any function's)."""
    import re
    if not is_mangled(name):
        return []
    p = parts(pretty(name))
    if p is None or "operator" in p[0]:
        return []
    comps, depth, cur = [], 0, ""
    i, text = 0, p[0]
    while i < len(text):
        ch = text[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if depth == 0 and text.startswith("::", i):
            comps.append(cur)
            cur, i = "", i + 2
            continue
        cur += ch
        i += 1
    comps.append(cur)

    def pattern(comp):
        out, depth = [], 0
        for ch in comp:
            if ch == "<":
                if depth == 0:
                    out.append(r"\s*<[^;{}()]*?>\s*")
                depth += 1
            elif ch == ">":
                depth -= 1
            elif depth == 0:
                out.append(re.escape(ch))
        return "".join(out)
    keep = min(2, len(comps))
    return [r"\s*::\s*".join(pattern(c) for c in comps[k:]) for k in range(len(comps) - keep + 1)]


def template_key(demangled):
    """The qualified name of a demangled function with every template argument list collapsed to
    <...> ("bound<...>", "Box<...>::get"), or None when it has none (or names an operator, whose
    < and > are no brackets)."""
    p = parts(demangled)
    if p is None or "operator" in p[0]:
        return None
    name = p[0] + ("<%s>" % p[1] if p[1] is not None else "")
    out, depth = [], 0
    for ch in name:
        if ch == "<":
            if depth == 0:
                out.append("<...>")
            depth += 1
        elif ch == ">":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    key = "".join(out)
    return key if "<...>" in key else None


def families(names):
    """[(template, [instance names])]: the instances of each template among the names (two or
    more), ordered by template (template_key)."""
    groups = {}
    for name, shown in demangle(names).items():
        key = template_key(shown) if is_mangled(name) else None
        if key is not None:
            groups.setdefault(key, []).append(name)
    return sorted((k, sorted(v)) for k, v in groups.items() if len(v) > 1)
