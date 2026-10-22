"""Proposed function names judged against the source the functions were compiled from (dread pilot
--score TRUTH --grounded PROVIDER): whether a name says what the function does, whatever its style.

A name compared with the one true name (dread.naming's words, dread pilot --judge) counts every other
valid name as a miss: task_sleep for vTaskDelay, heap_alloc for pvPortMalloc, sched_context_switch
for xPortPendSVHandler. Here a judge (a language model) reads each function's source instead, the names
of the functions it is shown with replaced by FUNCTION_k wherever they stand (traceENTER_vTaskDelay too,
and a call of one by another), and says of each proposal:
right (it states what the function does: any style, word order, abbreviation or synonym; more or less
specific than the authors' name is fine while it tells the function from its neighbours), partly
(about the right thing, but vague or incomplete, or one part of it wrong), or wrong (it says the
function does what it does not, or would mislead), with a reason of a few words. With the proposal's
sentence (names.tsv's what) shown, the sentence gets a verdict of its own and a wrong detail in it does
not make the name wrong: in a first form, which judged the name with its sentence together, a right
name was marked wrong for a wrong detail in the sentence.

Where the source comes from: the truth ELF's debug information (DWARF, read with pyelftools) gives
each function's entry, its name in the source and the file and line of its definition; the file is
found at the path DWARF names, else below one of the source roots given (its path relative to the
compilation's directory, or the longest end of its path that a file there has). The definition is
cut from that line to its closing brace (comments and strings skipped when matching braces), a return
type alone on the line before it included, the common indentation taken away, and cut at MAX_CHARS. A
function without debug information or whose file is not found (a library built elsewhere) is not
judged; the counts say how many.

The questions go a source file at a time (BATCH_FUNCTIONS and BATCH_CHARS at most), the sources first,
then the proposals numbered as the functions. When several runs are judged together (judge(), with
--against: this run and another), every run's question of a batch begins with the same sources, which a
provider that caches prompts keeps (dread.llm: Anthropic's prompt cache; the first question writes it
for a quarter more, the others read it for a tenth): on FreeRTOS GCC -Os (332 functions with a source,
about 270,000 tokens of it) a run judged alone by Sonnet 5.5 cost about $0.80, each run judged with it
about $0.35 (2026-10-22). Each question is asked on its own, so a verdict does not depend on another
run's names; it does depend on the other proposals of its batch (the company a judge sees moves its
verdicts a little), which is the same batch for every run.

What a verdict does not establish: that the judge read the code right, nor that a person would agree
(not measured yet). Measured on eight FreeRTOS naming runs (docs/decisions.md, 2026-10-22): two
judgings of one run gave the same verdict for 88-92 % of the functions and means within 0.012; the
runs came out in the order a pairwise judge against the true names had found; Haiku 5.5 as the judge
ranked them alike and agreed closely with Sonnet 5.5 (the area under the ROC curve of its verdicts on
Sonnet's right and wrong 0.97-0.98).
"""
import os
import re

MASK = "FUNCTION"           # what a function's own name becomes in the source a judge sees (FUNCTION_k)
MAX_CHARS = 8000            # a function's source at most (cut at a line, and said so)
BATCH_CHARS = 24000         # a question's sources at most ...
BATCH_FUNCTIONS = 10        # ... and functions
VERDICTS = ("right", "partly", "wrong")
VALUE = {"right": 1.0, "partly": 0.5, "wrong": 0.0}


class Source:
    """A function's definition in its source: entry, name (as the source has it), path, line, text."""

    def __init__(self, entry, name, path, line, text):
        self.entry, self.name, self.path, self.line, self.text = entry, name, path, line, text

    def shown_path(self, parts=3):
        """The last parts of its path, as a question shows it (FreeRTOS/Source/tasks.c)."""
        return "/".join(self.path.replace(os.sep, "/").split("/")[-parts:])


def dwarf_definitions(elf_path):
    """{entry: (name, path as DWARF gives it, compilation directory, line)} of the subprograms with
    code in an ELF's debug information: the name, file and line of their definition (through
    DW_AT_abstract_origin and DW_AT_specification where the DIE with the code has none: a clone's, an
    inlined function's out-of-line copy). {} without debug information; ImportError without
    pyelftools. A compilation unit whose line table pyelftools cannot read (MSP430's) gives none."""
    from elftools.elf.elffile import ELFFile
    out = {}
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        if not elf.has_dwarf_info():
            return {}
        dwarf = elf.get_dwarf_info()
        for cu in dwarf.iter_CUs():
            top = cu.get_top_DIE()
            comp = top.attributes.get("DW_AT_comp_dir")
            comp = comp.value.decode(errors="replace") if comp is not None else ""
            try:
                program = dwarf.line_program_for_CU(cu)
            except Exception:           # (pyelftools cannot read every line table: MSP430's, 2026-10)
                continue
            if program is None:
                continue
            files, dirs = program["file_entry"], program["include_directory"]
            version = program.header["version"]

            def file_path(index):
                if version >= 5:            # (DWARF 5 counts files and directories from 0, the 0th the CU's)
                    if index >= len(files):
                        return None
                    entry = files[index]
                    directory = dirs[entry.dir_index].decode(errors="replace") if entry.dir_index < len(dirs) else ""
                else:
                    if not 0 < index <= len(files):
                        return None
                    entry = files[index - 1]
                    directory = dirs[entry.dir_index - 1].decode(errors="replace") if entry.dir_index else comp
                name = entry.name.decode(errors="replace")
                return name if os.path.isabs(name) else os.path.join(directory, name)
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_subprogram" or "DW_AT_low_pc" not in die.attributes:
                    continue
                entry = die.attributes["DW_AT_low_pc"].value & ~1
                source, depth = die, 0
                while "DW_AT_decl_line" not in source.attributes and depth < 8:
                    ref = next((r for r in ("DW_AT_abstract_origin", "DW_AT_specification")
                                if r in source.attributes), None)
                    if ref is None:
                        break
                    try:
                        source = source.get_DIE_from_attribute(ref)
                    except Exception:
                        break
                    depth += 1
                name, scan = None, source
                for _ in range(8):          # (the name may sit one reference further than the line)
                    if "DW_AT_name" in scan.attributes:
                        name = scan.attributes["DW_AT_name"].value.decode(errors="replace")
                        break
                    ref = next((r for r in ("DW_AT_abstract_origin", "DW_AT_specification")
                                if r in scan.attributes), None)
                    if ref is None:
                        break
                    try:
                        scan = scan.get_DIE_from_attribute(ref)
                    except Exception:
                        break
                line = source.attributes.get("DW_AT_decl_line")
                index = source.attributes.get("DW_AT_decl_file")
                path = file_path(index.value) if index is not None else None
                if name and line is not None and path:
                    out[entry] = (name, path, comp, line.value)
    return out


def find_file(path, comp_dir, roots=()):
    """Where a source file DWARF names is on this machine: the path itself (joined to the compilation
    directory when relative), else below a root: the path as it is relative to it, then the file of the
    longest end of the path a file below it has. None when not found."""
    full = path if os.path.isabs(path) else os.path.join(comp_dir or "", path)
    if os.path.isfile(full):
        return os.path.normpath(full)
    parts = [p for p in os.path.normpath(path).replace(os.sep, "/").split("/") if p not in ("", ".", "..")]
    for root in roots:
        if not os.path.isabs(path) and os.path.isfile(os.path.join(root, path)):
            return os.path.normpath(os.path.join(root, path))
        for k in range(len(parts)):     # (the longest end first: Source/tasks.c before tasks.c)
            candidate = os.path.join(root, *parts[k:])
            if os.path.isfile(candidate):
                return os.path.normpath(candidate)
    return None


def blank_comments(text):
    """text with its comments, strings and character constants blanked (the same length, newlines
    kept), for matching braces."""
    out, i, n = list(text), 0, len(text)
    while i < n:
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
        elif text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
        elif text[i] in "\"'":
            end = i + 1
            while end < n and text[end] != text[i] and text[end] != "\n":
                end += 2 if text[end] == "\\" else 1
            end = min(end + 1, n)
        else:
            i += 1
            continue
        for k in range(i, end):
            if out[k] != "\n":
                out[k] = " "
        i = end
    return "".join(out)


def definition(text, line, name, max_chars=MAX_CHARS):
    """The definition of name that begins at line (1-based) of a file's text: from that line (a return
    type alone on the line before it too) to its closing brace, the common indentation away, cut at a
    line before max_chars with a note of what was left out. None when no body follows (a declaration)
    or the name is not in its first lines."""
    lines = text.split("\n")
    if not 0 < line <= len(lines):
        return None
    start = line - 1
    if start > 0 and re.match(r"\s*" + re.escape(name) + r"\b", lines[start]) and lines[start - 1].strip() and \
            re.match(r"^\s*[A-Za-z_][\w \t*]*$", lines[start - 1]):
        start -= 1
    offset = sum(len(x) + 1 for x in lines[:start])
    code = blank_comments(text)
    depth, i = 0, offset
    while i < len(code) and code[i] != "{":
        if code[i] == "(":
            depth += 1
        elif code[i] == ")":
            depth -= 1
        elif code[i] == ";" and depth <= 0:
            return None                     # (a declaration: no body)
        i += 1
    if i >= len(code):
        return None
    depth, end = 0, i
    while end < len(code):
        if code[end] == "{":
            depth += 1
        elif code[end] == "}":
            depth -= 1
            if depth == 0:
                break
        end += 1
    body = text[offset:end + 1].split("\n")
    if name not in "\n".join(body[:8]):
        return None
    indent = min((len(x) - len(x.lstrip()) for x in body if x.strip()), default=0)
    body = [x[indent:] if x[:indent].strip() == "" else x.lstrip() for x in body]
    out = "\n".join(body)
    if max_chars and len(out) > max_chars:
        cut = out[:max_chars]
        cut = cut[:cut.rfind("\n")] if "\n" in cut else cut
        out = cut + "\n/* ... %d more lines not shown */" % (len(body) - cut.count("\n") - 1)
    return out


def source_functions(elf_path, roots=(), max_chars=MAX_CHARS):
    """{entry: Source} of the functions whose definition the ELF's debug information names and whose
    file is found (find_file, below roots too); {} without debug information or pyelftools."""
    try:
        found = dwarf_definitions(elf_path)
    except ImportError:
        return {}
    files, out = {}, {}
    for entry, (name, path, comp, line) in sorted(found.items()):
        where = find_file(path, comp, roots)
        if where is None:
            continue
        if where not in files:
            try:
                with open(where, errors="replace") as f:
                    files[where] = f.read()
            except OSError:
                files[where] = None
        if files[where] is None:
            continue
        text = definition(files[where], line, name, max_chars)
        if text is not None:
            out[entry] = Source(entry, name, where, line, text)
    return out


def masked(text, name, mask=MASK):
    """text with name replaced by mask wherever it stands, inside longer identifiers too (a trace
    macro's traceENTER_vTaskDelay becomes traceENTER_FUNCTION)."""
    return re.sub(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", mask, text)


def batches(entries, sources, batch_chars=BATCH_CHARS, batch_functions=BATCH_FUNCTIONS):
    """[(shown path, [entries])]: the entries that have a source, a file at a time (files in the order
    of their first function's entry, functions by entry), each batch up to batch_functions functions and
    batch_chars characters of source (a larger function alone)."""
    by_file = {}
    for e in sorted(entries):
        if e in sources:
            by_file.setdefault(sources[e].path, []).append(e)
    out = []
    for path in sorted(by_file, key=lambda p: by_file[p][0]):
        current, size = [], 0
        for e in by_file[path]:
            length = len(sources[e].text)
            if current and (size + length > batch_chars or len(current) >= batch_functions):
                out.append((sources[current[0]].shown_path(), current))
                current, size = [], 0
            current.append(e)
            size += length
        if current:
            out.append((sources[current[0]].shown_path(), current))
    return out


SYSTEM = """You check names that a tool proposed for functions of a program, against the functions' source
code. The tool saw only the compiled code, not the source. You see the functions' sources, numbered,
each one's own name replaced by FUNCTION_ and its number wherever it stands (everything else as its
authors wrote it), and then what to judge."""

RUBRIC = """- "right": the name states the function's purpose correctly. Any naming style, word order,
  abbreviation or synonym is fine (delay_task, taskDelay, vSleepTicks and sleep_ticks can all be right
  for one function), and so is a name more or less specific than its authors' would be, as long as it
  is accurate and would let a reader tell this function from its neighbours.
- "partly": it is about the right thing but vague or incomplete (names the area but not what the
  function does in it, misses what sets it apart from similar functions), or one part of it is wrong.
- "wrong": it says the function does something it does not (another action, object or subsystem), or
  it would mislead a reader.
Judge by the code you see, not by how close the name comes to one you would have chosen."""

TASK_NAME = """The tool's names follow, numbered as the functions above. Say for each whether the name says
what the function does:
""" + RUBRIC + """
Answer with one ```json block that maps every number to its verdict; after partly and wrong a colon and
the reason in at most eight words, after right nothing:

```json
{"1": "right", "2": "partly: does not say it is the interrupt version", "3": "wrong: it receives, not sends"}
```"""

TASK_BOTH = """The tool's names follow, each with the tool's sentence on what the function does, numbered as
the functions above. Judge the name and the sentence apart. The name: does it say what the function
does?
""" + RUBRIC + """
The sentence: "right" when what it says is true of the code, "partly" when part of it is wrong or it
misses the main point, "wrong" when it is mostly wrong. A wrong detail in the sentence does not make
the name wrong. Answer with one ```json block that maps every number to the name's verdict and the
sentence's, apart by " | "; after partly and wrong a colon and the reason in at most eight words, after
right nothing:

```json
{"1": "right | right", "2": "partly: does not say it is the interrupt version | right", "3": "right | wrong: it suspends the task, not deletes it"}
```"""


def sources_part(path, entries, sources):
    """The first part of a batch's questions: its functions' sources, numbered, the name of each
    FUNCTION_k (its number) wherever it stands in any of them (a function calling another of the batch
    would show the other's name)."""
    names = sorted(((sources[e].name, k + 1) for k, e in enumerate(entries)), key=lambda x: -len(x[0]))
    parts = ["The source functions (file %s):" % path]
    for k, e in enumerate(entries):
        text = sources[e].text
        for name, number in names:
            text = masked(text, name, "%s_%d" % (MASK, number))
        parts.append("### Function %d\n```c\n%s\n```" % (k + 1, text))
    return "\n\n".join(parts)


def proposals_part(entries, proposals, sentence=True):
    """The second part: the task and the proposals ({entry: (name, what)}) numbered as the functions,
    those without a proposal left out."""
    lines = [TASK_BOTH if sentence else TASK_NAME, ""]
    for k, e in enumerate(entries):
        if e in proposals:
            name, what = proposals[e]
            lines.append("%d. %s: %s" % (k + 1, name, " ".join((what or "").split())) if sentence
                         else "%d. %s" % (k + 1, name))
    return "\n".join(lines)


ONE = re.compile(r"\s*(right|partly|wrong)\b\s*:?\s*(.*?)\s*$", re.I | re.S)


def parse(answer, numbers, sentence=True):
    """{number: (verdict, why, sentence verdict or None, why)} of an answer's ```json block (or, where it
    does not parse, of its lines "N": "..."), for the numbers asked; a number without a usable verdict
    is left out."""
    from dread.pilot import judged_json
    data = judged_json(answer)
    if any(str(k) not in data for k in numbers):    # (a reason with a quote in it breaks the JSON)
        for m in re.finditer(r'"(\d+)"\s*:\s*"((?:right|partly|wrong)[^\n]*?)"?\s*,?\s*$', answer or "", re.M | re.I):
            data.setdefault(m.group(1), m.group(2))
    out = {}
    for k in numbers:
        value = str(data.get(str(k), ""))
        parts = value.split("|")
        if sentence and len(parts) == 2:
            a, b = ONE.match(parts[0]), ONE.match(parts[1])
            if a and b:
                out[k] = (a.group(1).lower(), a.group(2), b.group(1).lower(), b.group(2))
        elif not sentence and len(parts) == 1:
            a = ONE.match(value)
            if a:
                out[k] = (a.group(1).lower(), a.group(2), None, "")
    return out


def judge(provider, runs, sources, sentence=True, out=print, tries=2, jobs=1):
    """{label: {entry: (verdict, why, sentence verdict or None, why)}} of runs ([(label, {entry: (name,
    what)})]) as a provider judges them against sources ({entry: Source}): a batch at a time
    (batches()), every run's question of a batch one after another so that they share its cached
    sources (dread.llm's cache parts: marked only when several runs share them, a cache written and
    never read costs a quarter more), a question asked again (up to tries) while verdicts are missing.
    sentence: the proposals' sentences are shown and judged apart (TASK_BOTH), else the names alone
    (TASK_NAME). jobs: batches asked at a time, each thread with a copy of provider whose counts are
    added to provider's at the end (Provider.spent). A function without a source is not judged."""
    import concurrent.futures
    import copy
    import threading
    from dread.llm import ProviderError
    results = {label: {} for label, _ in runs}
    wanted = set()
    for _, proposals in runs:
        wanted |= set(proposals)
    shared = sum(1 for _, proposals in runs if proposals) > 1
    local, copies, lock = threading.local(), [], threading.Lock()

    def asker():
        if jobs <= 1:
            return provider
        if getattr(local, "provider", None) is None:
            mine = copy.copy(provider)          # (its own last answer and counts: a thread's)
            mine.last, mine.used = {}, dict.fromkeys(provider.used, 0)
            local.provider = mine
            with lock:
                copies.append(mine)
        return local.provider

    def one(batch):
        path, entries = batch
        first = sources_part(path, entries, sources)
        got_all = []
        for label, proposals in runs:
            numbers = [k + 1 for k, e in enumerate(entries) if e in proposals]
            if not numbers:
                continue
            content = [{"text": first, "cache": shared}, {"text": proposals_part(entries, proposals, sentence)}]
            got = {}
            for _ in range(max(1, tries)):
                try:
                    answer = asker().ask(SYSTEM, [{"role": "user", "content": content}])
                except ProviderError as e:
                    out("dread pilot --grounded: no verdicts for %s (%s): %s" % (path, label, e))
                    break
                got.update(parse(answer, [k for k in numbers if k not in got], sentence))
                if len(got) == len(numbers):
                    break
            got_all.append((label, {entries[k - 1]: verdict for k, verdict in got.items()}))
        return got_all
    todo = batches(wanted, sources)
    if jobs <= 1:
        done = [one(b) for b in todo]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            done = list(pool.map(one, todo))
        for mine in copies:
            for key, value in mine.used.items():
                provider.used[key] = provider.used.get(key, 0) + value
    for got_all in done:
        for label, got in got_all:
            results[label].update(got)
    return results


def value(verdict):
    """A verdict's number: right 1, partly 0.5, wrong 0."""
    return VALUE[verdict]
