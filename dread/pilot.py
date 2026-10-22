"""dread pilot: the reconstruction loop run by code, a language model only for the judgement.

The tools decide everything a program can decide: which function is next, what the model is told,
whether its answer is right. The model does the part only judgement can do: write readable C from a
listing. For each function:

1. The question: the rules (behaviour exactly as the machine code's, faults included; dread.h; no
   variables of its own; no library calls), the function's flat listing (dread flat), its callers'
   constant arguments, the prototypes of what it calls (argument counts from dread.regflow), what
   its callers read after it, and the lifted C (dread lift), which is exact but unreadable.
2. The answer's ```c block goes into src/pilot/NAME.c, with auto("NAME") in tests/cases/pilot.py.
3. The checks, in order: dread recon (it builds), dread test (every case agrees and the cases run
   every instruction), dread integration --callers (no difference through the real callers), dread
   mutate --kill (no change to the C goes unnoticed). What fails goes back to the model with the
   tools' own words, up to --tries answers. A change no case and no search notices may be one that
   cannot alter behaviour: the model then argues each such survivor in a sentence (or corrects the
   C), and the arguments go into argued.tsv marked as the pilot's and not reviewed.
4. Accepted: the file stays, marked as written by the pilot and read by nobody; rejected: it is
   removed. Either way the conversation is kept in BUILD/pilot/IMAGE/NAME.md and the outcome in
   BUILD/pilot/IMAGE/results.tsv.

Without names, the candidates are the functions of the image's lifted table that src does not define
(their cases are known to cover them), smallest first. Providers: dread.llm (~/.config/dread/llm.toml).

Model-written C is its own kind: checked by cases, mutation and integration, but nobody has read it.
Count it apart from C people wrote, and read it before calling it understood.

Two more typed questions: --task input asks, for each function whose cases leave instructions unrun
(those of the lifted table, and those the last dread lift --check dropped as UNCOVERED, fewest missed
first), for one input at a time (JSON: argument registers, memory, peripheral answers); the emulator
decides, and an input that runs some of them becomes a written case; the run ends with what it ran
and what it cost. --task name asks for a name and a
sentence for FUN_ functions and collects the proposals in BUILD/pilot/IMAGE/names.tsv: no tool can
check a name, so none is applied. Its questions show a function's readable C (dread readable's view,
constants that point to text followed by the text) or, where no C can be made, its listing; they go
callees first, --jobs at a time, each showing the proposals made so far for the functions it calls
and its callers; a run started again goes on where the last one stopped. --score TRUTH compares the
proposals with true names (an ELF's symbols, or the functions table of a copy that has them).

A provider that declines DECLINED_IN_A_ROW questions in a row (stop reason "refusal" or
"content_filter") is asked no more in that run: its classifiers decline the same kind of question
again, and another provider may answer it. On 2026-10-20 Anthropic's Sonnet 5.5 and Opus 5.5 declined
every --task input question on FreeRTOS (a listing with instructions to reach), also with the purpose
stated, while Haiku 5.5 answered the same question.

    dread pilot [--target DIR] [--image NAME] [--provider NAME] [--tries N] [--limit N] [--no-lifted]
                [--no-mutate] [--no-integration] [--dry-run] [--task write|input|name|readable]
                [--jobs N] [--score TRUTH] [NAME...]
"""
import argparse
import contextlib
import io
import os
import re
import sys
import time

from dread import llm
from dread import target as tgt

# The prompts say "binary image", not "firmware image": Anthropic's API refused (stop reason
# "refusal", no text) the system prompt when "firmware" met the old wording of the first rule ("including
# what looks like a bug: fix nothing, add no check the code does not make") or code that jumps into RAM
# (a veneer), apparently read as keeping a firmware's flaws; either change alone let all of gizmo's
# questions through (2026-10-19, claude-sonnet-5-5).
SYSTEM = """You rewrite one function of a binary image in C. Your C is compiled with
    {compiler}
and linked over the original image in the function's place. Then the original machine code and
your C run side by side in an emulator on many inputs and must do exactly the same: the return
value, every memory write outside the stack, every peripheral access in the same order, and the
registers the callers rely on.

Rules:
- The function's name is exactly {name}. Its behaviour equals the machine code's exactly in every
  case, odd ones included: the comparison shows any change, an improvement as much as a mistake.
  Leave nothing out and add nothing.
- #include "dread.h": it defines u8, s8, u16, s16, u32, s32, u64, s64; VAR(type, address) for memory
  of the original at an address; VVAR(type, address) for memory that must be read or written every
  time; REG8, REG16, REG32(address) for peripheral registers (every access kept, in order).
- Declare each function you call with the prototype given; do not write their bodies.
- No global or static variables (the original's start-up would not set them up); constants are fine.
- No library calls (no memcpy, no division helpers): loops, and the image's own functions.
- Answer with one ```c block holding the whole file. A short note outside the block is allowed."""

SURVIVORS = """The C passes every case and the run through its callers, but no case notices these
changes to it, and a search for an input that would tell them apart found none:

{items}

For each of them, either argue on a line of its own, with its number, why the change cannot alter
what the function does:

{template}

or, when the C has something the machine code does not, answer with the whole corrected file in a
```c block instead."""

SURVIVORS_AGAIN = """The corrected C leaves the same change unnoticed as before:

{items}

The code is not the reason: no case and no search for an input tells the change apart. When you are
sure the C does what the machine code does, argue each of them, on a line of its own, with its number:

{template}"""

ARGUED = "argued by dread pilot ({label}), not reviewed: "

CUT = """The answer ran out of room ({spent}) before any code. Reason less: begin with the whole file in
one ```c block, and explain afterwards, if at all."""

FEEDBACK = {
    "code": "The answer has no ```c block. Answer with the whole file in one ```c block.",
    "build": "The C does not build:\n\n{text}\n\nAnswer with the whole corrected file.",
    "test": "The C builds, but differs from the machine code (original against your C):\n\n{text}\n\n"
            "Answer with the whole corrected file.",
    "mutate": "The mutation check of the C fails:\n\n{text}\n\nAnswer with the whole corrected file.",
    "argue": "Not every change has an argument yet:\n\n{text}\n\nArgue each on a line of its own that begins "
             "with its number, like this:\n\n{template}\n\nor answer with the whole corrected file.",
    "integration": "Run through its callers with the real callees, the C differs:\n\n{text}\n\n"
                   "Answer with the whole corrected file.",
}


def may_show(target, name):
    """Whether an LLM may be shown the function: any, unless the target's [llm] only list names the
    ones it may (a dread cclab build of a proprietary compiler: the probes, not the vendor's runtime
    library). Every question of the pilot is about one function and shows that function's code
    only; its callees appear by name."""
    return target.llm_only is None or name in target.llm_only


def shown(target, names, out=print):
    """The names an LLM may be shown, saying how many [llm] only held back."""
    kept = [n for n in names if may_show(target, n)]
    if len(kept) < len(names):
        out("dread pilot: %d of %d functions held back by [llm] only in dread.toml" % (len(names) - len(kept),
                                                                                      len(names)))
    return kept


def candidates(target, image, program, limit=None):
    """The lifted functions src does not define that an LLM may be shown, smallest first."""
    from dread import build
    lifted = build.read_names(image.table("lifted"))
    objects = build.compile_all(target, image, image.src, os.path.join(target.build, "pilot", image.name, "obj"),
                                quiet=True) if os.path.isdir(image.src) else []
    defined = build.defined_functions(objects)
    names = sorted((n for n in lifted if n in program.by_name and n not in defined and may_show(target, n)),
                   key=lambda n: (len(program.by_name[n].insns), n))
    return names[:limit] if limit else names


def result_words(program, flow, name):
    """How many registers of a function's result its callers read (1, 2 or 4), the way the MSP430
    lifter sizes results: the last of the first four argument registers (they carry results too:
    ARM's r0-r3, RISC-V's a0-a3, the MSP430's r12-r15) that callers read after a call and the
    function does not keep."""
    if not flow:
        return 1
    live = set(flow.kept_regs(name)) - set(flow.preserved_regs(name))
    n = max((i + 1 for i, r in enumerate(program.arch.arg_regs(4)) if r in live), default=1)
    return 4 if n > 2 else n


def ctype(program, words):
    """dread.h's unsigned type of so many registers (u16 to u64; C returns no more than 64 bits)."""
    return "u%d" % min(64, words * program.arch.reg_bits)


def prototypes(program, flow, fn):
    """C declarations of what fn calls: as many register-wide arguments as each callee reads (u32
    on ARM and RISC-V, u16 on the MSP430), and a result as wide as its callers read."""
    from dread.lift import direct_callees
    word = ctype(program, 1)
    out = []
    for name in sorted(direct_callees(program, fn)):
        args = program.arch.arg_regs(4)
        reads = flow.read_regs(name) if flow else args
        n = max((args.index(r) + 1 for r in reads if r in args), default=0)
        out.append("%s %s(%s);" % (ctype(program, result_words(program, flow, name)), name,
                                   ", ".join("%s a%d" % (word, i) for i in range(n)) or "void"))
    return out


def question(target, image, program, fn, flow=None, lifted=None):
    """(system, user) text of the write question."""
    from dread import callsites, constants, flat
    compiler = " ".join(target.compiler.command("cc") + target.compiler.cflags)
    system = SYSTEM.format(compiler=compiler, name=fn.name)
    lines = flat.listing(program, fn, flow, callers=flat.callers_of(program))
    parts = ["The function %s at 0x%x (%s, %d instructions):\n\n```\n%s\n```" % (
        fn.name, fn.entry, fn.isa, len(fn.insns), "\n".join(lines))]
    if flow is not None:
        reads = [r for r in flow.read_regs(fn.name) if r in program.arch.arg_regs(4)]
        parts.append("It reads %s at its entry (its arguments)." % (", ".join(reads) or "no argument register"))
        words = result_words(program, flow, fn.name)
        if words > 1:
            regs = program.arch.arg_regs(4)[:words]
            parts.append("Its callers read %s after it: it returns a %d-bit value (%s)." % (
                " and ".join(regs) if words == 2 else ", ".join(regs), min(64, words * program.arch.reg_bits),
                ctype(program, words)))
    calls = []
    for callee, caller, address, kind in callsites.sites(program, [fn.name]):
        values = constants.at(program, caller, address, program.arch.arg_regs(4))
        shown = ", ".join("%s=%s" % (r, flat.describe_value(program, v)) for r, v in sorted(values.items()))
        calls.append("- %s at 0x%x%s" % (caller.name, address, ": " + shown if shown else ""))
    if calls:
        parts.append("Its callers, with the constant arguments they pass:\n" + "\n".join(calls[:20]))
    protos = prototypes(program, flow, fn)
    if protos:
        parts.append("Declare what it calls like this:\n\n```c\n%s\n```" % "\n".join(protos))
    if lifted:
        parts.append("This C does exactly what the machine code does, one statement per instruction "
                     "(dread lift): right, but unreadable. Use it as the reference for the behaviour, "
                     "not as the shape of your answer:\n\n```c\n%s```" % lifted)
    return system, "\n\n".join(parts)


def extract_c(answer):
    """The first ```c block of an answer, or None."""
    m = re.search(r"```(?:c|C)\s*\n(.*?)```", answer or "", re.S)
    return m.group(1) if m and m.group(1).strip() else None


class Workspace:
    """Where a candidate lives in the target while it is checked: src/pilot/NAME.c and an auto() line
    in tests/cases/pilot.py."""

    def __init__(self, target, image):
        self.target, self.image = target, image
        self.dir = os.path.join(image.src, "pilot")
        self.cases = os.path.join(image.cases, "pilot.py")

    def path(self, name):
        return os.path.join(self.dir, name + ".c")

    def put(self, name, entry, label, code):
        os.makedirs(self.dir, exist_ok=True)
        with open(self.path(name), "w") as f:
            f.write("/* %s at 0x%x: written by dread pilot (%s); checked by its cases, the mutation check\n"
                    " * and the integration run; not read by a person. */\n" % (name, entry, label))
            f.write(code if code.endswith("\n") else code + "\n")
        lines = self._case_lines()
        if 'auto("%s")' % name not in lines:
            self._write_cases(lines + ['auto("%s")' % name])

    def remove(self, name):
        if os.path.exists(self.path(name)):
            os.remove(self.path(name))
        self._write_cases([l for l in self._case_lines() if l != 'auto("%s")' % name])

    def _case_lines(self):
        if not os.path.exists(self.cases):
            return []
        with open(self.cases) as f:
            return [l.rstrip("\n") for l in f if l.strip() and not l.startswith("#")]

    def _write_cases(self, lines):
        os.makedirs(os.path.dirname(self.cases), exist_ok=True)
        with open(self.cases, "w") as f:
            f.write("# Cases of the functions dread pilot wrote: generated from the original.\n")
            f.write("".join(l + "\n" for l in lines))


def check(target, image, name, mutate_check=True, integration_check=True, jobs=None):
    """(ok, stage, text, survivors): the tools' verdict on the candidate in the workspace; survivors
    [(source line, change)] when only the mutation check objects (the cases and a search for a
    distinguishing input notice nothing: an equivalent change, or code the original does not have)."""
    from dread import build, integration, mutate, overlay, runner
    try:
        payload = build.build(target, image, quiet=True)
    except overlay.OverlayError as e:
        return False, "build", str(e)[:3000], []
    runner.prepare(target, image, payload)
    lines = []
    failed, uncovered, results = runner.run([name], only=True, out=lines.append, jobs=jobs)
    if failed or uncovered or not results:
        return False, "test", "\n".join(lines)[:4000] or "no case ran", []
    if integration_check:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = integration.main(["--target", target.root, "--image", image.name, "--callers", name])
        if rc:
            return False, "integration", buf.getvalue()[:4000], []
    if mutate_check:
        out = []
        try:
            caught, survivors, unbuilt, away = mutate.check(target, image, name, jobs=jobs, out=out.append,
                                                            kill_survivors=True)
        except (RuntimeError, KeyError) as e:
            return False, "mutate", str(e), []
        if survivors:
            source = open(os.path.join(image.src, "pilot", name + ".c")).read().splitlines()
            return False, "mutate", "\n".join(out)[:4000], [(source[i].strip(), what) for i, new, what in survivors]
    return True, "accepted", "", []


def argue_template(numbers):
    """The lines an argument answer consists of, with the changes' own numbers (a template that says
    "N" gets copied as it stands)."""
    return "\n".join("    %d: equivalent: <one sentence: why change %d alters nothing>" % (k, k) for k in numbers)


def argue(image, name, pending, answer, label):
    """Record the model's arguments for the survivors (argued.tsv, marked as the pilot's and not
    reviewed); returns the survivors still without one, as text."""
    from dread import mutate
    given = {}
    for m in re.finditer(r"^[\s>*_`-]*(\d+|N)[*_`]*\s*[:.)]\s*[*_`]*equivalent[*_`]*\s*:\s*(.+?)\s*$", answer,
                         re.M | re.I):
        if m.group(1).isdigit():
            given[int(m.group(1))] = m.group(2).replace("\t", " ")
        elif len(pending) == 1:              # the template's N copied: with one change it can only mean 1
            given.setdefault(1, m.group(2).replace("\t", " "))
    missing = ["%d. `%s`: %s" % (k, line, what) for k, (line, what) in enumerate(pending, 1) if k not in given]
    if missing:
        return missing
    path = mutate.argued_path(image)
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write("# Survivors of dread mutate that cannot be caught, with the argument why the change alters "
                    "nothing.\n# function<TAB>source line (stripped)<TAB>change<TAB>argument\n")
        for k, (line, what) in enumerate(pending, 1):
            f.write("%s\t%s\t%s\t%s%s\n" % (name, line, what, ARGUED.format(label=label), given[k]))
    return []


def unargue(image, name):
    """Take the pilot's arguments for a function out of argued.tsv again (it was rejected)."""
    from dread import mutate
    path = mutate.argued_path(image)
    if not os.path.exists(path):
        return
    with open(path) as f:
        lines = f.readlines()
    keep = [l for l in lines if not (l.startswith(name + "\t") and "\targued by dread pilot (" in l)]
    if keep != lines:
        with open(path, "w") as f:
            f.writelines(keep)


def ended(info):
    """How an answer ended, for the log: finish reason, tokens, reasoning."""
    parts = []
    if info.get("finish"):
        parts.append("finish: %s" % info["finish"])
    if info.get("tokens"):
        parts.append("%d tokens" % info["tokens"])
    if info.get("reasoning"):
        parts.append("%d characters of reasoning" % info["reasoning"])
    return ", ".join(parts)


def pilot_one(target, image, program, provider, name, tries=3, flow=None, use_lifted=True, mutate_check=True,
              integration_check=True, out=print, jobs=None, endless=None):
    """Ask, check and ask again until accepted or out of tries; returns (outcome, answers, stage).
    The survivors question gets an answer of its own when it comes with the last try (it only asks
    for arguments). endless: the never-returning functions, when known (for the lifter)."""
    from dread import build, lift
    fn = program.by_name[name]
    text = None
    if use_lifted:
        try:
            text = lift.make_lifter(program, flow, endless).lift(fn).text
        except lift.Refused:
            text = None
    system, user = question(target, image, program, fn, flow, text)
    work = Workspace(target, image)
    messages = [{"role": "user", "content": user}]
    log = ["# %s, asked %s\n\n## System\n\n%s\n\n## Question\n\n%s" % (name, provider.label(), system, user)]
    stage, outcome, answers, pending = "code", "rejected", 0, []
    asked = []                              # the survivors the last survivors question named
    started = time.time()
    limit, extra = tries, False
    while answers < limit:
        answers += 1
        try:
            answer = provider.ask(system, messages)
        except Exception as e:              # the provider failed: no point in trying again now
            log.append("## Provider error\n\n%s" % e)
            stage = "provider"
            break
        info = getattr(provider, "last", {}) or {}
        log.append("## Answer %d%s\n\n%s" % (answers, " (%s)" % ended(info) if ended(info) else "", answer))
        if info.get("finish") in llm.Provider.REFUSED:
            outcome, stage = "refused", "provider refusal"      # another model may answer it
            break
        code = extract_c(answer)
        if code is None and not pending and info.get("finish") in llm.Provider.CUT:
            spent = ended(info).split(", ", 1)[-1] if info.get("tokens") or info.get("reasoning") else "the token limit"
            feedback = CUT.format(spent=spent)
            stage = "cut"
            log.append("## The tools, after answer %d (%s)\n\n%s" % (answers, stage, feedback))
            messages += [{"role": "assistant", "content": answer or "(empty)"}, {"role": "user", "content": feedback}]
            continue
        if code is None and pending:        # an answer to the survivors question
            missing = argue(image, name, pending, answer, provider.label())
            if not missing:
                outcome, stage = "accepted", "argued"
                break
            ok, stage, report = False, "argue", "\n".join(missing)
            numbers = [int(line.split(".", 1)[0]) for line in missing]
        elif code is None:
            ok, stage, report = False, "code", ""
        else:
            work.put(name, fn.entry, provider.label(), code)
            ok, stage, report, pending = check(target, image, name, mutate_check, integration_check, jobs)
        if ok:
            outcome = "accepted"
            break
        if stage == "mutate" and pending:
            again = sorted(pending) == sorted(asked)
            asked = list(pending)
            feedback = (SURVIVORS_AGAIN if again else SURVIVORS).format(
                items="\n".join("%d. `%s`: %s" % (k, line, what) for k, (line, what) in enumerate(pending, 1)),
                template=argue_template(range(1, len(pending) + 1)))
            if answers == limit and not extra:      # the arguments get an answer of their own
                limit, extra = limit + 1, True
        else:
            feedback = FEEDBACK[stage].format(text=report, template=argue_template(numbers) if stage == "argue" else "")
        log.append("## The tools, after answer %d (%s)\n\n%s" % (answers, stage, feedback))
        messages += [{"role": "assistant", "content": answer}, {"role": "user", "content": feedback}]
    if outcome != "accepted":
        work.remove(name)
        unargue(image, name)
        try:
            build.build(target, image, quiet=True)  # the rebuilt image as it was
        except Exception:
            pass
    folder = os.path.join(target.build, "pilot", image.name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name + ".md"), "w") as f:
        f.write("\n\n".join(log) + "\n\n## Outcome\n\n%s after %d answers (%s)\n" % (outcome, answers, stage))
    results = os.path.join(folder, "results.tsv")
    new = not os.path.exists(results)
    with open(results, "a") as f:
        if new:
            f.write("function\toutcome\tanswers\tstage\tseconds\tprovider\n")
        f.write("%s\t%s\t%d\t%s\t%.0f\t%s\n" % (name, outcome, answers, stage, time.time() - started, provider.label()))
    out("%-24s %s after %d answer%s (%s)" % (name, outcome, answers, "" if answers == 1 else "s", stage))
    return outcome, answers, stage


# ---- the other typed questions ----

INPUT_SYSTEM = """You find inputs for one function of a binary image, so that all of its code can be
tested in an emulator. An input gives the argument registers, the memory the function reads and the
values peripheral registers answer with:

    {{"args": [r0, r1, r2, r3],
     "mem": [[address, size in bytes, value], ...],
     "poll": [[register address, from the n-th read on, value], ...]}}

Numbers may be integers or hex strings ("0x20001000"). Memory not given reads as zero; for buffers
use the scratch area 0x{scratch:x} to 0x{end:x}. A pointer to code (a callback) must be the entry of a
function, never an address inside one. Answer with one ```json block holding one input."""

NAME_SYSTEM = """You name functions of a binary image from their code. The code below is C made from
the machine code by tools (variables named after the registers and stack slots that held them,
memory at fixed addresses written g_ADDRESS, io_ADDRESS for device registers, rom_ADDRESS in the
image), or the machine code itself where no C could be made. A function without a name is written
FUN_ and its address; some names of other functions are proposals made from their code earlier, likely
but not certain. A comment after a constant shows the text it points to. A name says what the function
does, in snake_case, with a prefix for its subsystem when one is clear (uart_, gpio_, timer_, fs_,
ui_); follow the style of the names you see. Answer with exactly two lines:

name: <the name>
what: <one sentence: what the function does>"""


READABLE_SYSTEM = """You name the variables of one function of a binary image. Its C below was made
from the machine code by tools and does exactly what the machine code does; its variables are named
after the registers and stack slots that held them (r0, r4, r2_1, s36), its memory accesses are
written with their offsets. Give each variable a name that says what it holds, and say in one
sentence what the function does. The names of the functions it calls and of its callers may tell you
more; follow the style of the names you see (a project's prefixes, its case).

Answer with one ```json block:

```json
{"what": "<one sentence>", "names": {"r0": "<name>", "r4": "<name>"}}
```

A name is a C identifier of at most 48 characters, no C keyword, no name the C already uses for
something else (a function it calls, a type such as u32), no two variables alike. Leave out a
variable you cannot name. Nothing else changes: the names are put into the C by a tool, which checks
that the C still compiles to the same code."""


GLOBALS_SYSTEM = """You name the global variables and device registers of a binary image. Its C was
made from the machine code by tools; memory at a fixed address is written as a name that says only
where it is: g_20029aac (RAM), io_40004004 (a device register), rom_c6e4 (read-only memory), with a
suffix when it is read or written narrower or wider than a word (g_20029aac_u8). Each is shown with
lines of the image's C that use it, under the name of the function they are in. Give each a name
that says what it holds; the functions' names show the project's style (its prefixes, its case):
follow it.

Answer with one ```json block mapping the shown names to yours:

```json
{"g_20029aac": "<name>", "io_40004004": "<name>"}
```

A name is a C identifier of at most 48 characters, no C keyword, no function's name, no two alike.
Leave out one you cannot name."""
DEFAULT_GLOBAL = re.compile(r"^(g|io|rom)_[0-9a-f]+(_[us]\d+)?$")


def global_uses(program, context, lifter, names, per=2):
    """What the readable C of the functions names says about its memory at fixed addresses:
    ({key: {"name": its default name, "functions": [names], "uses": [(function, line)]}},
    {function: {keys}}), at most per lines a function."""
    from dread import cparse, naming, readable
    uses, by_function = {}, {}
    for tree, fn, macros in readable.readable_trees(program, context, lifter, names):
        name = fn.name
        chosen = {naming.global_key(*at): gname for gname, at in macros.items()}
        body = cparse.unparse(fn, notes=False, style="readable").splitlines()
        by_function[name] = set(chosen)
        for key, gname in chosen.items():
            u = uses.setdefault(key, {"name": gname, "functions": [], "uses": []})
            u["functions"].append(name)
            pattern = re.compile(r"\b%s\b" % re.escape(gname))
            u["uses"] += [(name, line.strip()[:160]) for line in body if pattern.search(line)][:per]
    return uses, by_function


def name_globals(target, image, program, provider, keys, uses, batch=40, shown_functions=4, out=print):
    """Ask for names of the memory at fixed addresses in keys that the readable_names table does not
    name yet, batch at a time, each with its uses in up to shown_functions functions; keep the names
    that are C identifiers no function or other global has. Returns how many were named."""
    from dread import naming
    table = naming.load(image, "globals")
    todo = [k for k in keys if k not in table and k in uses]
    taken = set(program.by_name) | {e.get("name") for e in table.values()} | naming.KEYWORDS
    folder = os.path.join(target.build, "pilot", image.name)
    os.makedirs(folder, exist_ok=True)
    named = 0
    for start in range(0, len(todo), batch):
        part = todo[start:start + batch]
        lines = []
        for key in part:
            u = uses[key]
            lines.append("%s (%s, in %d functions):" % (u["name"], key.replace(" ", ", "), len(u["functions"])))
            fns = []
            for fname, line in u["uses"]:
                if fname not in fns:
                    if len(fns) >= shown_functions:
                        continue
                    fns.append(fname)
                lines.append("    %s: %s" % (fname, line))
        user = "\n".join(lines)
        by_default = {uses[k]["name"]: k for k in part}
        try:
            answer = provider.ask(GLOBALS_SYSTEM, [{"role": "user", "content": user}])
        except Exception as e:
            out("dread pilot: the globals' names: the provider: %s" % e)
            break
        with open(os.path.join(folder, "globals-%d.md" % (start // batch)), "w") as f:
            f.write("# Globals, asked %s\n\n## System\n\n%s\n\n## Question\n\n%s\n\n## Answer\n\n%s\n" % (
                provider.label(), GLOBALS_SYSTEM, user, answer))
        if (getattr(provider, "last", {}) or {}).get("finish") in llm.Provider.REFUSED:
            out("dread pilot: the globals' names: the provider declined")
            break
        import json
        m = re.search(r"```(?:json)?\s*\n(.*?)```", answer or "", re.S)
        try:
            proposed = json.loads(m.group(1)) if m else None
        except ValueError:
            proposed = None
        if not isinstance(proposed, dict):
            out("dread pilot: the globals' names: no ```json block in the answer")
            continue
        refused = []
        for shown_name, new in proposed.items():
            key = by_default.get(shown_name)
            if key is None:
                continue
            if (not isinstance(new, str) or not naming.IDENT.match(new) or new in taken
                    or DEFAULT_GLOBAL.match(new)):
                refused.append("%s=%s" % (shown_name, new))
                continue
            taken.add(new)
            naming.store(image, key, {"name": new, "by": provider.label()}, part="globals")
            named += 1
        out("dread pilot: the globals' names: %d of %d named%s" % (
            sum(1 for k in part if k in naming.load(image, "globals")), len(part),
            "; refused %s" % ", ".join(refused[:8]) if refused else ""))
    return named


STRUCTS_SYSTEM = """You name the structures of a binary image and their fields. Its C was made from
the machine code by tools: memory reached through a pointer is declared as structures named s_1,
s_2 ..., their fields named after their offsets (f_0, f_4, f_2c: hexadecimal), gaps as bytes (pad_8).
Each structure is shown with its definition and lines of the image's C that use its fields, under
the name of the function they are in. Give each structure a name that says what it is and each field
a name that says what it holds; the functions' names show the project's style (its prefixes, its
case): follow it.

Answer with one ```json block:

```json
{"s_1": {"name": "<name>", "fields": {"f_0": "<name>", "f_4": "<name>"}}}
```

A name is a C identifier of at most 48 characters, no C keyword; no two structures alike, no two
fields of one structure alike. Leave out what you cannot name."""


def struct_uses(program, context, lifter, names, per=3):
    """{s_N: [(function, line)]} of the lines of the functions' typed readable C that reach a field of
    a plan structure (dread.shapes), at most per lines a function, with the default names (s_N, f_2c,
    the registers' names): what a question about the structures shows."""
    import copy
    from dread import lift, readable
    context = copy.copy(context)            # (a context of its own: readable.transform keeps its results
    context.names, context.structs, context._struct_names = {}, {}, None    # by context, names apart)
    uses = {k: [] for k in context.plan.structs}
    for name in names:
        typed = context.plan.typed.get(name)
        fn_p = program.by_name.get(name)
        if not typed or fn_p is None:
            continue
        try:
            text = lifter.lift(fn_p).text
        except lift.Refused:
            continue
        out = readable.transform(name, text, context=context)
        if not out:
            continue
        lines = [line.split("  /*")[0].strip() for line in out.splitlines()]
        for k in sorted(set(typed.values())):
            pattern = re.compile(r"\b(%s)->" % "|".join(re.escape(v) for v, kk in typed.items() if kk == k))
            uses.setdefault(k, []).extend((name, line[:160]) for line in lines if pattern.search(line))
    for k in uses:
        by_fn, kept = {}, []
        for fname, line in uses[k]:
            if by_fn.get(fname, 0) < per:
                kept.append((fname, line))
                by_fn[fname] = by_fn.get(fname, 0) + 1
        uses[k] = kept
    return uses


def name_structs(target, image, program, provider, plan, uses, wanted, taken=(), batch=8, shown_functions=4,
                 out=print):
    """Ask for names of the plan's structures in wanted and of their fields, batch at a time, each with
    its definition and its uses in up to shown_functions functions; keep in the readable_names table's
    structs part what dread.naming.struct_names accepts (keyed by the structure's first anchor, with
    all its anchors: the function:variable pairs pointing to it). Returns how many were named."""
    import json
    from dread import naming, shapes
    table = naming.load(image, "structs")
    done = naming.struct_names(plan, table, taken)
    todo = [k for k in wanted if k in plan.structs and k not in done and uses.get(k)]
    folder = os.path.join(target.build, "pilot", image.name)
    os.makedirs(folder, exist_ok=True)
    named = 0
    for start in range(0, len(todo), batch):
        part = todo[start:start + batch]
        blocks = []
        for k in part:
            fns = []
            lines = [shapes.struct_text(k, plan.structs[k])]
            for fname, line in uses[k]:
                if fname not in fns:
                    if len(fns) >= shown_functions:
                        continue
                    fns.append(fname)
                lines.append("    %s: %s" % (fname, line))
            blocks.append("\n".join(lines))
        user = "\n\n".join(blocks)
        try:
            answer = provider.ask(STRUCTS_SYSTEM, [{"role": "user", "content": user}])
        except Exception as e:
            out("dread pilot: the structures' names: the provider: %s" % e)
            break
        with open(os.path.join(folder, "structs-%d.md" % (start // batch)), "w") as f:
            f.write("# Structures, asked %s\n\n## System\n\n%s\n\n## Question\n\n%s\n\n## Answer\n\n%s\n" % (
                provider.label(), STRUCTS_SYSTEM, user, answer))
        if (getattr(provider, "last", {}) or {}).get("finish") in llm.Provider.REFUSED:
            out("dread pilot: the structures' names: the provider declined")
            break
        m = re.search(r"```(?:json)?\s*\n(.*?)```", answer or "", re.S)
        try:
            proposed = json.loads(m.group(1)) if m else None
        except ValueError:
            proposed = None
        if not isinstance(proposed, dict):
            out("dread pilot: the structures' names: no ```json block in the answer")
            continue
        for k in part:
            entry = proposed.get(k)
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                continue
            fields = {}
            for fkey, fname in (entry.get("fields") or {}).items():
                fm = re.match(r"^f_([0-9a-f]+)$", str(fkey))
                if fm:
                    fields[str(int(fm.group(1), 16))] = fname
            anchors = naming.struct_anchors(plan, k)
            candidate = dict(table, **{anchors[0]: {"name": entry["name"], "fields": fields, "anchors": anchors,
                                                     "by": provider.label()}})
            if k not in naming.struct_names(plan, candidate, taken):
                continue                    # (refused: no C name, taken, or another structure has it)
            table = candidate
            naming.store(image, anchors[0], table[anchors[0]], part="structs")
            named += 1
        out("dread pilot: the structures' names: %d of %d named" % (
            sum(1 for k in part if k in naming.struct_names(plan, table, taken)), len(part)))
    return named


def parse_names(answer):
    """(what, {variable: name}) of an answer's ```json block, or None."""
    import json
    m = re.search(r"```(?:json)?\s*\n(.*?)```", answer or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    names = data.get("names") if isinstance(data, dict) else None
    what = data.get("what") if isinstance(data, dict) else None
    if not isinstance(names, dict) or not isinstance(what, str):
        return None
    return what.strip(), {str(k): v for k, v in names.items() if v != k}


def callers_of(program, name):
    """The names of the functions that call name directly."""
    from dread.lift import direct_callees
    return sorted(fn.name for fn in program.functions if fn.name != name and name in direct_callees(program, fn))


def name_readable(target, image, program, provider, name, context, lifter, tries=2, out=print):
    """Ask for the names of a function's variables in its readable C (dread readable), put them in with
    dread.naming and keep them in the image's readable_names table when the C still compiles to the
    same code; tell the model what was wrong and ask again up to tries answers. Returns the table
    entry, or None. The conversation goes to BUILD/pilot/IMAGE/NAME.readable.md."""
    import copy
    from dread import cparse, lift, naming, readable
    fn_p = program.by_name[name]
    try:
        text = lifter.lift(fn_p).text
    except lift.Refused as e:
        out("%-24s no readable C: %s" % (name, e))
        return None
    made = readable.readable_tree(name, text, context=context)
    if made is None:
        out("%-24s no readable C: its lifted C is not the dialect" % name)
        return None
    tree, fn = made
    digest = naming.digest(fn)
    known = context.names.get(name) if context is not None else None
    if known and known.get("digest") == digest:
        out("%-24s named already" % name)
        return known
    naming.apply_globals(tree, fn, context.globals if context is not None else None,
                         context.memory if context is not None else None)
    if context is not None:                 # (the structures, named where the table names them)
        from dread import shapes
        shapes.apply_types(tree, fn, context.plan, digest, names=context.struct_names())
    unnamed = readable.print_file(tree, fn)
    callers = [c for c in callers_of(program, name) if may_show(target, c)][:12]
    user = "The function %s (readable C):\n\n```c\n%s\n```" % (name, cparse.unparse(fn, notes=False, style="readable"))
    if callers:
        user += "\n\nIt is called by: %s." % ", ".join(callers)
    messages = [{"role": "user", "content": user}]
    log = ["# %s, asked %s\n\n## System\n\n%s\n\n## Question\n\n%s" % (name, provider.label(), READABLE_SYSTEM, user)]
    folder = os.path.join(target.build, "pilot", image.name)
    os.makedirs(folder, exist_ok=True)
    entry, why = None, "no answer"
    for _ in range(tries):
        try:
            answer = provider.ask(READABLE_SYSTEM, messages)
        except Exception as e:
            why = "the provider: %s" % e
            break
        log.append("## Answer\n\n%s" % answer)
        if (getattr(provider, "last", {}) or {}).get("finish") in llm.Provider.REFUSED:
            why = "the provider declined"
            break
        parsed = parse_names(answer)
        if parsed is None:
            problems = ['no ```json block with "what" (a sentence) and "names" (an object)']
        else:
            what, names = parsed
            problems = naming.problems(tree, fn, names)
            if not problems:
                tree2 = copy.deepcopy(tree)
                fn2 = tree2.function(name)
                naming.rename(fn2, names)
                if naming.same_code(target, image, unnamed, readable.print_file(tree2, fn2),
                                    os.path.join(folder, "naming")):
                    entry = {"digest": digest, "names": names, "what": what, "by": provider.label()}
                    naming.store(image, name, entry)
                    break
                problems = ["with these names the C compiles to other code"]
        why = "; ".join(problems)
        messages += [{"role": "assistant", "content": answer},
                     {"role": "user", "content": "These names cannot be used: %s. Answer again with the whole "
                                                  "```json block." % why}]
        log.append("## Back to the model\n\n%s" % messages[-1]["content"])
    with open(os.path.join(folder, name + ".readable.md"), "w") as f:
        f.write("\n\n".join(log) + "\n")
    if entry is None:
        out("%-24s no names: %s" % (name, why[:160]))
        return None
    out("%-24s %d names: %s" % (name, len(entry["names"]), entry["what"][:120]))
    return entry


def number(value):
    return int(value, 0) if isinstance(value, str) else int(value)


def parse_input(answer):
    """The input of an answer's ```json block as a case, or None."""
    import json
    m = re.search(r"```(?:json)?\s*\n(.*?)```", answer or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        args = [number(v) & 0xFFFFFFFF for v in data.get("args", [])][:4]
        mem = [[number(a), number(n), number(v)] for a, n, v in data.get("mem", [])]
        poll = [[number(a), number(n), number(v)] for a, n, v in data.get("poll", [])]
    except (ValueError, TypeError, KeyError):
        return None
    return {"args": args + [0] * (4 - len(args)), "mem": mem, "poll": poll, "returns": [], "calls": [], "frame": 0,
            "limit": 200_000}


def missed_instructions(target, image, names):
    """{name: [addresses]} the cases of each function leave unrun (dread test's UNCOVERED)."""
    from dread import build, runner
    payload = build.build(target, image, quiet=True)
    suite = runner.prepare(target, image, payload)
    names = [n for n in names if n in suite.cases or n in suite.auto]
    lines = []
    old = os.environ.get("DREAD_ALL_MISSED")
    os.environ["DREAD_ALL_MISSED"] = "1"
    try:
        runner.run(names, only=True, out=lines.append)
    finally:
        if old is None:
            del os.environ["DREAD_ALL_MISSED"]
    out = {}
    for text in lines:
        for line in text.splitlines():
            m = re.match(r"UNCOVERED (\S+): .* never ran: (.*)$", line)
            if m:
                out[m.group(1)] = [int(a, 16) for a in m.group(2).split()]
    return out


def lift_missed(target, image, program):
    """{name: [addresses]} the last dread lift --check dropped because its cases left them unrun
    (UNCOVERED in its verdicts), functions that never return left out (their cases are loop cases):
    what the lifter leaves for want of inputs."""
    path = os.path.join(target.build, "lift-check", image.name, "verdicts.tsv")
    if not os.path.exists(path):
        return {}
    from dread import noreturn
    endless = set(noreturn.find(program))
    out = {}
    with open(path) as f:
        next(f, None)
        for line in f:
            row = (line.rstrip("\n").split("\t") + ["", "", ""])[:4]
            name, verdict, reason, missed = row
            if verdict == "dropped" and reason.startswith("UNCOVERED") and missed and name in program.by_name \
                    and name not in endless:
                out[name] = [int(a, 16) for a in missed.split()]
    return out


DECLINED_IN_A_ROW = 3       # questions a provider declined one after another before a run stops asking it
FAILED_IN_A_ROW = 20        # questions that failed on the provider's side (after its own retries) in a row


class Asking:
    """Counts what a provider declines over a run: go_on() before each function's questions says
    whether to ask them, asked() after them notes how the last answer ended; after DECLINED_IN_A_ROW
    declined in a row nothing more is asked (see the module's docstring)."""

    def __init__(self, provider, out=print):
        self.provider, self.out = provider, out
        self.row = self.declined = self.done = 0

    @property
    def stopped(self):
        return self.row >= DECLINED_IN_A_ROW

    def go_on(self):
        if self.stopped:
            return False
        self.provider.last = {}             # (a provider error leaves no finish: not a refusal)
        return True

    def asked(self):
        self.done += 1
        if (getattr(self.provider, "last", {}) or {}).get("finish") not in llm.Provider.REFUSED:
            self.row = 0
            return
        self.row += 1
        self.declined += 1
        if self.stopped:
            self.out("dread pilot: %s declined %d questions in a row; the rest are not asked of it (its "
                     "classifiers decline the same kind of question again; another provider may answer: "
                     "--provider)" % (self.provider.label(), self.row))


def input_task(target, image, program, provider, asked, tries=3, flow=None, out=print):
    """--task input over [(name, missed addresses)]; returns the summary line (without the cost)."""
    asking = Asking(provider, out)
    whole = ran = total = 0
    for name, addresses in asked:
        if not asking.go_on():
            break
        still = find_inputs(target, image, program, provider, name, addresses, tries, flow, out)
        asking.asked()
        whole += not still
        ran += len(addresses) - len(still)
        total += len(addresses)
    return "%d functions%s, %d with every missed instruction run, %d of %d missed instructions run%s" % (
        asking.done, " of %d" % len(asked) if asking.done < len(asked) else "", whole, ran, total,
        ", %d declined by the provider" % asking.declined if asking.declined else "")


def wild_call(gen, program, fn, case):
    """The first address `fn` calls through a register that is not a function's entry, or None. An
    input that points a callback into the middle of a function runs code there that the rebuilt image
    does not have (it replaces the function): deepseek-flash once passed _fwalk_sglue a callback
    into _fwalk_sglue itself, which the original ran and the rebuild faulted on. The target is the
    first block behind one of fn's calls through a register (a call to the next instruction, as an
    svc that runs on, is none). Not seen: a call through a veneer (bl to a bx rN stub) reaches its
    target a block later."""
    sites = {a: program.insn_size(a) for a in fn.insns if program.arch.flow(program.insn(a))[0] == "icall"}
    if not sites:
        return None
    m = gen.machine()
    m.plan = []
    gen.apply(m, case)
    m.watch_calls(program, sites, anywhere=True)        # (dread.emu: any address that is no entry)
    m.may_hang = bool(case.get("hang"))
    try:
        m.call(fn.entry, *case["args"], max_instructions=case.get("limit", 200_000))
    except Exception:
        pass
    return m.wild_calls[0][1] if m.wild_calls else None


CALLEE_LINES = 80          # a callee's listing goes into the input question up to this many lines ...
CALLEES_LINES = 400        # ... and all of them up to this many


def callee_listings(program, fn, flow=None):
    """The flat listings of the functions fn calls directly, in address order, as long as they fit
    (CALLEE_LINES each, CALLEES_LINES in all), and the names of those left out. Without names a model
    cannot tell what a helper reads (2026-10-21, FreeRTOS with every name made FUN_address: Haiku
    "can't see the disassembly of the helpers this function calls", and its inputs for vTaskSuspend
    faulted in the list functions, which it had set up by their names before)."""
    from dread import flat
    from dread.lift import direct_callees
    shown, left, room = [], [], CALLEES_LINES
    for callee in sorted((program.by_name[n] for n in direct_callees(program, fn)), key=lambda f: f.entry):
        text = flat.listing(program, callee, flow)
        if len(text) > CALLEE_LINES or len(text) > room:
            left.append(callee.name)
            continue
        shown.append("\n".join(text))
        room -= len(text)
    return shown, left


def find_inputs(target, image, program, provider, name, missed, tries=3, flow=None, out=print):
    """Ask for inputs that run the missed instructions; each one that runs some becomes a written
    case of the function (extra_cases, "by" the pilot). The question holds the function's listing and
    its callees' (callee_listings); an input that does not finish gets back where and why it stopped
    (Generator.attempt). Returns the addresses still unrun."""
    import json
    from dread import autocase, flat
    fn = program.by_name[name]
    gen = autocase.Generator(target, image, program=program)
    scratch = target.memory.scratch_base or 0
    system = INPUT_SYSTEM.format(scratch=scratch, end=scratch + (target.memory.scratch_size or 0))
    marked = []
    for line in flat.listing(program, fn, flow, callers=flat.callers_of(program)):
        m = re.match(r"\s+([0-9a-f]{8})\s", line)
        marked.append((">> " if m and int(m.group(1), 16) in missed else "   ") + line)
    still = set(missed)
    shown, left = callee_listings(program, fn, flow)
    callees = ""
    if shown:
        callees = ("\n\nThe functions it calls, for what they read and write:\n\n```\n%s\n```%s" % (
            "\n\n".join(shown), "\n(Not shown, too long: %s.)" % ", ".join(left) if left else ""))
    user = ("The function %s:\n\n```\n%s\n```%s\n\nThe instructions marked >> never ran under any input yet: "
            "%s. Give one input that runs as many of them as you can." % (
                name, "\n".join(marked), callees, " ".join("0x%x" % a for a in sorted(still))))
    messages = [{"role": "user", "content": user}]
    log = ["# Inputs for %s, asked %s\n\n## System\n\n%s\n\n## Question\n\n%s" % (name, provider.label(), system, user)]
    for k in range(1, tries + 1):
        if not still:
            break
        try:
            answer = provider.ask(system, messages)
        except Exception as e:
            log.append("## Provider error\n\n%s" % e)
            break
        log.append("## Answer %d\n\n%s" % (k, answer))
        if (getattr(provider, "last", {}) or {}).get("finish") in llm.Provider.REFUSED:
            log.append("## The provider declined to answer")
            break
        case = parse_input(answer)
        if case is None:
            feedback = "The answer has no ```json block with an input of the form given. Answer with one."
        else:
            ran, why = gen.attempt(fn.entry, fn.insns, case, case["limit"], check_image=True)
            new = ran & still if not why else set()
            wild = wild_call(gen, program, fn, case) if new else None
            if why:
                reached = sorted(ran & still)
                feedback = ("The input does not finish: %s.%s Give another one." % (
                    why, " Before it stopped it ran %s of the marked ones (an input counts only when it "
                    "finishes)." % " ".join("0x%x" % a for a in reached) if reached else ""))
            elif not new:
                feedback = "The input runs, but none of the marked instructions. Give another one."
            elif wild is not None:
                owner = program.function_at(wild)
                feedback = ("The input makes %s call 0x%x through a pointer it gave: %s, not a function's entry. "
                            "The rebuilt image has other code there. A pointer to code must be a function's "
                            "entry. Give another one." % (name, wild, "inside %s" % owner.name if owner else
                                                          "outside every function"))
            else:
                still -= new
                case.update({"by": "dread pilot (%s)" % provider.label(),
                             "why": "runs %s" % " ".join("0x%x" % a for a in sorted(new))})
                path = os.path.join(image.extra_cases, name + ".json")
                old = json.load(open(path))["cases"] if os.path.exists(path) else []
                os.makedirs(image.extra_cases, exist_ok=True)
                with open(path, "w") as f:
                    json.dump({"cases": old + [case]}, f, indent=1, sort_keys=True)
                feedback = ("It runs %s and is kept. %s" % (
                    " ".join("0x%x" % a for a in sorted(new)),
                    "Still never run: %s. Give another input." % " ".join("0x%x" % a for a in sorted(still))
                    if still else "All marked instructions ran."))
        log.append("## The tools, after answer %d\n\n%s" % (k, feedback))
        messages += [{"role": "assistant", "content": answer}, {"role": "user", "content": feedback}]
    folder = os.path.join(target.build, "pilot", image.name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name + ".inputs.md"), "w") as f:
        f.write("\n\n".join(log) + "\n")
    out("%-24s %d of %d missed instructions run by the inputs found" % (name, len(missed) - len(still), len(missed)))
    return sorted(still)


NAME_CHARS = 24000         # the code a name question shows at most (the rest cut, and said so)
HEX = re.compile(r"\b0x([0-9a-fA-F]+)u?\b")
IDENT_WORD = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


class Namer:
    """What the questions for function names need, made once for a run: the readable C's context and
    lifter, the image's readable table, the callers of every function."""

    def __init__(self, target, image, program, flow=None):
        from dread import build, flat, lift, readable
        self.target, self.image, self.program, self.flow = target, image, program, flow
        self.context = readable.Context(target, image, program, flow)
        self.lifter = lift.make_lifter(program, flow)
        self.readable = set(build.read_names(image.table("readable")))
        self.callers = flat.callers_of(program)
        self.views, self.prepared = {}, set()

    def prepare(self, names):
        """Make the readable C of names in a pool of workers (dread.readable views), before any thread
        asks: questions then only put names in."""
        from dread import readable
        self.views.update(readable.views(self.program, self.context, self.lifter, list(names)))
        self.prepared |= set(names)

    def code(self, fn):
        """(text, form) of a function's code for a question: its readable C ("readable" when the
        image's readable table has it, the gate having checked it; "readable, unchecked" else), or its
        listing ("listing") when it does not lift or its C is not the dialect."""
        from dread import flat, lift, readable
        if fn.name in self.prepared:
            text = self.views.get(fn.name)
        else:
            try:
                text = readable.view(fn.name, self.lifter.lift(fn).text, context=self.context)
            except lift.Refused:
                text = None
        if text is not None:
            return text, "readable" if fn.name in self.readable else "readable, unchecked"
        return "\n".join(flat.listing(self.program, fn, self.flow, callers=self.callers)), "listing"

    def question(self, fn, shown_as=None):
        """(text, form) of the question for a function's name: its code (code()) with each constant
        that points to text in the image followed by that text, other functions by their names in
        shown_as ({name: name shown}: the run's proposals) where it has one, and its callers."""
        from dread.constants import string_at
        program = self.program
        text, form = self.code(fn)
        if form != "listing":               # (the listing shows the texts already)
            def note(m):
                s = string_at(program, int(m.group(1), 16))
                return m.group(0) + (' /* "%s" */' % s.replace("*/", "* /") if s else "")
            text = HEX.sub(note, text)
        if shown_as:
            text = IDENT_WORD.sub(lambda m: shown_as.get(m.group(0), m.group(0)) if m.group(0) != fn.name
                                  else m.group(0), text)
        if len(text) > NAME_CHARS:
            text = text[:NAME_CHARS] + "\n... (cut: %d more characters)" % (len(text) - NAME_CHARS)
        if form == "listing":
            user = "The function %s, its machine code (no C could be made):\n\n```\n%s\n```" % (fn.name, text)
        else:
            user = "The function %s, as C made from its machine code:\n\n```c\n%s\n```" % (fn.name, text)
            callers = []
            for caller, _ in self.callers.get(fn.name, []):
                shown = (shown_as or {}).get(caller, caller)
                if shown not in callers and may_show(self.target, caller):
                    callers.append(shown)
            if callers:
                more = " and %d more" % (len(callers) - 12) if len(callers) > 12 else ""
                user += "\n\nIt is called by: %s%s." % (", ".join(callers[:12]), more)
        return user, form


def name_problem(program, proposed):
    """Why a proposed function name cannot be used, or None: a C identifier of at most 48
    characters, not a name the image's functions have, not one of the unnamed form (FUN_1a2b)."""
    from dread.samecode import UNNAMED
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,47}$", proposed or ""):
        return "a name is a C identifier of at most 48 characters"
    if proposed in program.by_name:
        return "%s is the name of another function" % proposed
    if UNNAMED.match(proposed):
        return "%s has the form of a function without a name" % proposed
    return None


def parse_name(answer):
    """(name, what) of an answer's two lines, or None."""
    proposed = re.search(r"^\s*\**name\**\s*:\s*\**`?([A-Za-z_][A-Za-z0-9_]*)`?\**\s*$", answer or "", re.M | re.I)
    what = re.search(r"^\s*\**what\**\s*:\s*(.+?)\s*$", answer or "", re.M | re.I)
    if not proposed or not what:
        return None
    return proposed.group(1), what.group(1)


def ask_name(provider, program, name, user, tries=2):
    """(proposed, what, log, why) of asking for one function's name: a name that cannot be used
    (name_problem) or an answer without the two lines goes back with the reason, up to tries
    answers; proposed is None when none came."""
    messages = [{"role": "user", "content": user}]
    log = ["# %s, asked %s\n\n## Question\n\n%s" % (name, provider.label(), user)]
    why = "no answer"
    for _ in range(tries):
        provider.last = {}
        try:
            answer = provider.ask(NAME_SYSTEM, messages)
        except Exception as e:
            return None, None, log, "the provider: %s" % e
        log.append("## Answer\n\n%s" % answer)
        if (getattr(provider, "last", {}) or {}).get("finish") in llm.Provider.REFUSED:
            return None, None, log, "the provider declined"
        parsed = parse_name(answer)
        why = "no two lines name: and what:" if parsed is None else name_problem(program, parsed[0])
        if why is None:
            return parsed[0], parsed[1], log, None
        messages += [{"role": "assistant", "content": answer},
                     {"role": "user", "content": "That name cannot be used: %s. Answer again with the two lines."
                                                 % why}]
        log.append("## Back to the model\n\n%s" % messages[-1]["content"])
    return None, None, log, why


def names_path(target, image):
    return os.path.join(target.build, "pilot", image.name, "names.tsv")


def read_proposals(target, image):
    """{address: (proposed, what)} of the image's names.tsv, the last proposal of each address."""
    out = {}
    path = names_path(target, image)
    if os.path.exists(path):
        for line in open(path).read().splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) >= 4:
                try:
                    out[int(parts[0], 16)] = (parts[2], parts[3])
                except ValueError:
                    continue
    return out


def keep_proposal(target, image, fn, proposed, what, provider, form):
    """A line of the image's names.tsv: address, now, proposed, what, provider, shown (the form of
    the code the question showed)."""
    path = names_path(target, image)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write("address\tnow\tproposed\twhat\tprovider\tshown\n")
        f.write("0x%x\t%s\t%s\t%s\t%s\t%s\n" % (fn.entry, fn.name, proposed, what.replace("\t", " "),
                                                provider.label(), form))


def propose_name(target, image, program, provider, name, flow=None, out=print, namer=None, shown_as=None,
                 tries=2):
    """Ask for one function's name and a sentence; a proposal goes to BUILD/pilot/IMAGE/names.tsv,
    never into the tables (no tool can check a name), the conversation to
    BUILD/pilot/IMAGE/names/NAME.md. Returns (name, what) or None."""
    namer = namer or Namer(target, image, program, flow)
    fn = program.by_name[name]
    user, form = namer.question(fn, shown_as)
    proposed, what, log, why = ask_name(provider, program, name, user, tries)
    write_log(target, image, name, log)
    if proposed is None:
        out("%-24s no proposal: %s" % (name, why))
        return None
    keep_proposal(target, image, fn, proposed, what, provider, form)
    out("%-24s proposed %s: %s" % (name, proposed, what))
    return proposed, what


def write_log(target, image, name, log):
    folder = os.path.join(target.build, "pilot", image.name, "names")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name + ".md"), "w") as f:
        f.write("\n\n".join(log) + "\n")


def callees_first(program, names):
    """The functions of names in rounds, as lists: a function's round comes after those of the
    functions it calls among names (direct calls and tail calls), functions that call each other in a
    circle sharing one round, so that a question can show what its callees were named."""
    from dread.lift import direct_callees
    nameset = set(names)
    calls = {n: sorted(c for c in direct_callees(program, program.by_name[n]) if c in nameset and c != n)
             for n in names}
    index, low, on, stack, comp, comps = {}, {}, set(), [], {}, []
    for root in names:                      # Tarjan's components, without recursion
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            v, i = work.pop()
            if i == 0:
                index[v] = low[v] = len(index)
                stack.append(v)
                on.add(v)
            for k in range(i, len(calls[v])):
                w = calls[v][k]
                if w not in index:
                    work += [(v, k + 1), (w, 0)]
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            else:
                if low[v] == index[v]:
                    members = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp[w] = len(comps)
                        members.append(w)
                        if w == v:
                            break
                    comps.append(members)
                if work:
                    u = work[-1][0]
                    low[u] = min(low[u], low[v])
    level = {}
    for c, members in enumerate(comps):     # (Tarjan finishes a component after those it reaches)
        level[c] = 1 + max([level[comp[w]] for m in members for w in calls[m] if comp[w] != c] or [-1])
    rounds = [[] for _ in range(1 + max(level.values(), default=-1))]
    for n in names:
        rounds[level[comp[n]]].append(n)
    return rounds


def name_functions(target, image, program, make_provider, names, flow=None, jobs=4, tries=2, out=print):
    """Proposals for the functions of names, callees first (callees_first), jobs questions at a time
    with a provider each (make_provider() makes one); each question shows the proposals made so far
    for the other functions in place of their names. A function names.tsv has a proposal for already
    is not asked again, its proposal shown. Returns (proposed, asked, spent: summed over the
    providers)."""
    import concurrent.futures
    import threading
    namer = Namer(target, image, program, flow)
    known = read_proposals(target, image)
    shown_as = {program.by_entry[a].name: p for a, (p, _) in known.items() if a in program.by_entry}
    namer.prepare([n for n in names if program.by_name[n].entry not in known])     # (before any thread)
    local, providers, lock = threading.local(), [], threading.Lock()

    def provider():
        if getattr(local, "provider", None) is None:
            local.provider = make_provider()
            with lock:
                providers.append(local.provider)
        return local.provider

    def one(item):
        name, user, form = item
        p = provider()
        proposed, what, log, why = ask_name(p, program, name, user, tries)
        return name, form, proposed, what, log, why, p, (getattr(p, "last", {}) or {}).get("finish")

    proposed = asked = declined_row = failed_row = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for k, members in enumerate(callees_first(program, names)):
            todo = [n for n in members if program.by_name[n].entry not in known]
            if not todo:
                continue
            items = [(n,) + namer.question(program.by_name[n], shown_as) for n in todo]
            for name, form, got, what, log, why, p, finish in pool.map(one, items):
                asked += 1
                write_log(target, image, name, log)
                if got is None:
                    out("%-24s no proposal: %s" % (name, why))
                    declined_row = declined_row + 1 if finish in llm.Provider.REFUSED else 0
                    failed_row = failed_row + 1 if why.startswith("the provider:") else 0
                    continue
                declined_row = failed_row = 0
                fn = program.by_name[name]
                keep_proposal(target, image, fn, got, what, p, form)
                known[fn.entry] = (got, what)
                shown_as[name] = got
                proposed += 1
                out("%-24s proposed %s (%s): %s" % (name, got, form, what))
            if declined_row >= DECLINED_IN_A_ROW:
                out("dread pilot: the provider declined %d questions in a row; no more are asked" % declined_row)
                break
            if failed_row >= FAILED_IN_A_ROW:
                out("dread pilot: the provider failed %d questions in a row; no more are asked (the run "
                    "goes on where it stopped when started again)" % failed_row)
                break
    spent = {"answers": 0, "input": 0, "output": 0, "cost": None}
    for p in providers:
        s = p.spent()
        for key in ("answers", "input", "output"):
            spent[key] += s[key]
        if s.get("cost") is not None:
            spent["cost"] = (spent["cost"] or 0.0) + s["cost"]
    return proposed, asked, spent


JUDGE_SYSTEM = """You compare names of functions of a program. In each numbered pair the first name
was given by people who read the code knowing the whole program; the second was proposed from one
function's code. Say for each pair whether the second names the same function as the first: "same"
(the same purpose, in other words or abbreviations too), "close" (a related purpose: more general,
more specific, or one part of what the first says), or "different". Answer with one ```json block that
maps every pair's number to its word:

```json
{"1": "same", "2": "close", "3": "different"}
```"""
VERDICTS = ("same", "close", "different")


def judge_names(provider, pairs, batch=50, out=print):
    """{key: "same", "close" or "different"} of pairs ([(key, true name, proposed name)]) as a
    provider judges them, batch pairs a question; a pair without a usable verdict is left out. A
    language model's judgement, a second measure beside the word scores (synonyms and abbreviations:
    buf_len against buffer_length shares no word)."""
    import json
    out_map = {}
    for start in range(0, len(pairs), batch):
        part = pairs[start:start + batch]
        user = "\n".join("%d. %s | %s" % (k + 1, t, p) for k, (_, t, p) in enumerate(part))
        try:
            answer = provider.ask(JUDGE_SYSTEM, [{"role": "user", "content": user}])
        except Exception as e:
            out("dread pilot --judge: no verdicts for pairs %d-%d: %s" % (start + 1, start + len(part), e))
            continue
        m = re.search(r"```(?:json)?\s*\n(.*?)```", answer or "", re.S)
        try:
            data = json.loads(m.group(1)) if m else {}
        except ValueError:
            data = {}
        for k, (key, _, _) in enumerate(part):
            v = str(data.get(str(k + 1), "")).strip().lower() if isinstance(data, dict) else ""
            if v in VERDICTS:
                out_map[key] = v
    return out_map


def score_names(target, image, truth_path, out=print, judge=None):
    """The image's proposals (names.tsv) against true names (dread.naming.function_truth): prints a
    line a function into BUILD/pilot/IMAGE/names-score.tsv and the totals; judge, a provider: its
    verdicts as well (judge_names)."""
    from dread import naming
    proposals = {a: p for a, (p, _) in read_proposals(target, image).items()}
    rows, t = naming.score_functions(proposals, naming.function_truth(truth_path))
    verdicts = judge_names(judge, [(r["address"], r["true"], r["proposed"]) for r in rows], out=out) \
        if judge is not None and rows else {}
    path = os.path.join(target.build, "pilot", image.name, "names-score.tsv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("address\ttrue\tproposed\texact\tsame_words\toverlap\tsame_prefix\tjudged\n")
        for r in rows:
            f.write("0x%x\t%s\t%s\t%d\t%d\t%.2f\t%s\t%s\n" % (
                r["address"], r["true"], r["proposed"], r["exact"], r["same_words"], r["overlap"],
                "" if r["prefix"] is None else int(r["prefix"]), verdicts.get(r["address"], "")))
    for v in VERDICTS:
        t["judged_" + v] = sum(1 for x in verdicts.values() if x == v)
    t["judged"] = len(verdicts)
    n = t["functions"]
    if not n:
        out("dread pilot --score: no proposal at an address the true names have (%s)" % truth_path)
        return t

    def pc(k, of=n):
        return "%d (%.0f %%)" % (t[k], 100.0 * t[k] / of) if of else "%d" % t[k]
    out("dread pilot --score: %d proposals with a true name: exact %s, the same words %s, a word shared %s, "
        "half the words or more %s, mean overlap %.2f; the same prefix %d of %d with prefixes (%s)" % (
            n, pc("exact"), pc("same_words"), pc("shared_word"), pc("half_shared"), t["overlap"],
            t["same_prefix"], t["prefixed"], path))
    if judge is not None:
        out("dread pilot --score: judged by %s, %d of %d: the same %s, close %s, different %s" % (
            judge.label(), t["judged"], n, pc("judged_same", t["judged"]), pc("judged_close", t["judged"]),
            pc("judged_different", t["judged"])))
    return t


def print_usage(month=None):
    """The usage ledger's figures for a month, per provider (dread.llm.usage_summary)."""
    import time
    month = month or time.strftime("%Y-%m")
    summary = llm.usage_summary(month)
    if not summary:
        print("dread pilot: no answers recorded in %s (%s)" % (month, llm.USAGE))
        return 0
    total = 0.0
    for name, e in sorted(summary.items()):
        cost = "$%.2f" % e["cost"] if e["cost"] is not None else "no prices in llm.toml"
        total += e["cost"] or 0
        print("%-16s %-22s %5d answers %10d tokens in %9d out  %s" % (name, e["model"], e["answers"], e["input"],
                                                                     e["output"], cost))
    print("%s: $%.2f where prices are known (%s)" % (month, total, llm.USAGE))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread pilot", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--provider", help="a provider of ~/.config/dread/llm.toml (default: its default)")
    parser.add_argument("--tries", type=int, default=3, help="answers per function at most (default 3)")
    parser.add_argument("--limit", type=int, help="functions at most")
    parser.add_argument("--no-lifted", action="store_true", help="do not show the model the lifted C")
    parser.add_argument("--no-mutate", action="store_true", help="skip the mutation check")
    parser.add_argument("--no-integration", action="store_true", help="skip the integration run")
    parser.add_argument("--dry-run", action="store_true",
                        help="ask nothing: print each question (write), or the functions and what would be asked "
                             "about them (input, name)")
    parser.add_argument("--usage", nargs="?", const="", metavar="YYYY-MM",
                        help="print the tokens (and costs, where llm.toml gives prices) of a month's answers; ask nothing")
    parser.add_argument("--jobs", type=int, default=4, help="--task name: questions asked at a time (default 4)")
    parser.add_argument("--score", metavar="TRUTH",
                        help="--task name: compare the proposals (names.tsv) with true names (an ELF's symbols, "
                             "or a functions table of a copy that has them) and ask nothing")
    parser.add_argument("--judge", metavar="PROVIDER",
                        help="with --score: a provider also judges each pair the same, close or different")
    parser.add_argument("--task", choices=("write", "input", "name", "readable"), default="write",
                        help="write C (default), find inputs for unrun instructions, propose names, or name the "
                             "variables of readable C (dread readable)")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # a line a function as it ends, also into a file (a run its
        sys.stdout.reconfigure(line_buffering=True)     # timeout ended had kept none of them)
    if args.usage is not None:
        return print_usage(args.usage or None)
    try:
        target, image = tgt.from_args(args)
    except tgt.TargetError as e:
        print("dread pilot: %s" % e, file=sys.stderr)
        return 1
    from dread import llm, regflow
    from dread.program import Program
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    if args.task == "name" and args.score:
        judge = None
        if args.judge:
            try:
                judge = llm.load(args.judge)
            except llm.ProviderError as e:
                print("dread pilot: %s" % e, file=sys.stderr)
                return 1
        return 0 if score_names(target, image, args.score, judge=judge)["functions"] else 1
    if args.task != "write":
        provider = None
        if not args.dry_run:                # (a dry run asks nothing, so it needs no provider)
            try:
                provider = llm.load(args.provider)
            except llm.ProviderError as e:
                print("dread pilot: %s" % e, file=sys.stderr)
                return 1
        if args.task == "readable":
            from dread import build, lift, naming, readable
            everything = shown(target, build.read_names(image.table("readable")), out=lambda *_: None)
            names = shown(target, args.names) if args.names else everything
            names = names[:args.limit] if args.limit else names
            if args.dry_run:
                print("\n".join(names))
                print("dread pilot --task readable --dry-run: %d functions would be asked about; nothing asked"
                      % len(names))
                return 0
            context = readable.Context(target, image, program, flow)
            lifter = lift.make_lifter(program, flow)
            uses, by_function = global_uses(program, context, lifter, sorted(set(everything) | set(names)))
            wanted = sorted(set().union(*(by_function.get(n, set()) for n in names))) if names else []
            name_globals(target, image, program, provider, wanted, uses)
            context.globals = naming.load(image, "globals")
            if context.plan.structs:            # (then the structures, which the variables' C shows)
                suses = struct_uses(program, context, lifter, sorted(set(everything) | set(names)))
                swanted = sorted({k for n in names for k in context.plan.typed.get(n, {}).values()})
                taken = {e.get("name") for e in context.globals.values() if isinstance(e, dict)}
                name_structs(target, image, program, provider, context.plan, suses, swanted, taken)
                context.structs, context._struct_names = naming.load(image, "structs"), None
            asking, named = Asking(provider), 0
            for name in names:
                if not asking.go_on():
                    break
                named += name_readable(target, image, program, provider, name, context, lifter,
                                       tries=args.tries) is not None
                asking.asked()
            spent = provider.spent()
            print("dread pilot --task readable: %d of %d functions named; %d answers, %d tokens in, %d out" % (
                named, len(names), spent["answers"], spent["input"], spent["output"]))
            return 0
        if args.task == "name":
            from dread.samecode import UNNAMED
            names = shown(target, args.names) if args.names else [
                fn.name for fn in program.functions if UNNAMED.match(fn.name) and may_show(target, fn.name)]
            names = [n for r in callees_first(program, names) for n in r]
            names = names[:args.limit] if args.limit else names
            if args.dry_run:
                known = read_proposals(target, image)
                todo = [n for n in names if program.by_name[n].entry not in known]
                print("\n".join(todo))
                print("dread pilot --task name --dry-run: %d functions would be asked about, callees first (%d "
                      "proposed already); nothing asked" % (len(todo), len(names) - len(todo)))
                return 0
            made, asked, spent = name_functions(target, image, program, lambda: llm.load(args.provider), names,
                                                flow, jobs=args.jobs, tries=min(args.tries, 3))
            cost = " ($%.2f)" % spent["cost"] if spent["cost"] is not None else ""
            print("dread pilot --task name: %d of %d asked functions got a proposal (%s); %d answers, %d tokens "
                  "in, %d out%s" % (made, asked, names_path(target, image), spent["answers"], spent["input"],
                                    spent["output"], cost))
            return 0
        missed = missed_instructions(target, image, args.names or [fn.name for fn in program.functions])
        for name, addresses in lift_missed(target, image, program).items():      # what the lifter left
            if not args.names or name in args.names:
                missed.setdefault(name, addresses)
        missed = {n: missed[n] for n in shown(target, sorted(missed))}
        asked = sorted(missed.items(), key=lambda kv: (len(kv[1]), kv[0]))[:args.limit]     # fewest first
        if args.dry_run:
            for name, addresses in asked:
                print("%-34s %d missed instructions" % (name, len(addresses)))
            print("dread pilot --task input --dry-run: %d functions, %d missed instructions would be asked "
                  "about; nothing asked" % (len(asked), sum(len(a) for _, a in asked)))
            return 0
        started = time.time()
        line = input_task(target, image, program, provider, asked, args.tries, flow)
        spent = provider.spent()            # (its own answers: the ledger also holds runs beside it)
        print("dread pilot --task input: %s; %d answers, %d tokens in, %d out%s, %.0f s" % (
            line, spent["answers"], spent["input"], spent["output"],
            ", $%.2f" % spent["cost"] if spent["cost"] is not None else "", time.time() - started))
        return 0
    names = shown(target, args.names) if args.names else candidates(target, image, program, args.limit)
    if not names:
        print("dread pilot: nothing to do (no lifted function that src does not define)")
        return 0
    from dread import noreturn
    endless = noreturn.find(program)        # once, for every lifter below
    if args.dry_run:
        from dread import lift
        for name in names:
            fn = program.by_name[name]
            text = None
            if not args.no_lifted:
                try:
                    text = lift.make_lifter(program, flow, endless).lift(fn).text
                except lift.Refused:
                    pass
            system, user = question(target, image, program, fn, flow, text)
            print("=" * 72 + "\n" + system + "\n" + "-" * 72 + "\n" + user)
        return 0
    try:
        provider = llm.load(args.provider)
    except llm.ProviderError as e:
        print("dread pilot: %s" % e, file=sys.stderr)
        return 1
    from dread.util.polite import polite
    polite()
    accepted, asking = 0, Asking(provider)
    for name in names:
        if not asking.go_on():
            break
        outcome, _, _ = pilot_one(target, image, program, provider, name, args.tries, flow, not args.no_lifted,
                                  not args.no_mutate, not args.no_integration, endless=endless)
        asking.asked()
        accepted += outcome == "accepted"
    print("dread pilot: %d of %d accepted%s; conversations in %s" % (
        accepted, asking.done, ", %d declined by the provider" % asking.declined if asking.declined else "",
        os.path.relpath(os.path.join(target.build, "pilot", image.name))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
