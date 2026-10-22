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
proposals with true names (an ELF's symbols, or the functions table of a copy that has them), --judge
PROVIDER adds a model's verdict per pair, and --against NAMES_TSV (another run's names.tsv) instead
the judge's choice, for each function the two runs named otherwise, of the name closer to the true
one, asked in both orders. --refine asks again, callers first, for every function with a proposal:
its question shows its callers' names and its first one. --behaviour shows in each question what the
function did in runs of its cases (dread.behaviour: its calls, device accesses, writes and results on
a few generated inputs). --samples K asks each function K times and keeps the name most answers agree
with (dread.naming.vote), the agreement its confidence; --gate C shows a question only the proposals
of confidence C or more; --passes N asks again, callers first and callees first in turn, what a
changed question would show otherwise, until nothing shown changes; --ablate asks each function also
with no other proposal shown and flags a name that differs then. By default each function is asked
five times and a second pass runs (--samples 5 --passes 2: measured on FreeRTOS, docs/decisions.md
2026-10-22; about nine times the answers of one question in one pass), and the functions' groups are
named first and shown in their questions (--subsystems, below; --no-subsystems turns it off). --score
with --judge also gives the verdicts per confidence (the calibration) and per ablation flag.

--task subsystems names the groups dread groups finds from the code alone (shared memory, texts and
devices, addresses taken, neighbours in memory): one question a group (--samples times, the prefix
most answers agree with kept) with its size, its functions' names where they have one, the texts they
point to, the devices they use, its callers and callees outside it and its functions' code (cut to
SUBSYSTEM_CHARS); a name, a prefix and a sentence, kept in BUILD/pilot/IMAGE/subsystems.tsv with the
grouping in groups.tsv. --task name --subsystems (the default since 2026-10-22: on FreeRTOS its names
were judged closer to the source's than those of the votes and passes alone, 153 to 51) does that
first and shows each function's question its group's name, prefix and sentence; --score then also
counts the proposals that begin with their group's prefix and sets each group beside its most common
source file (names-subsystems.tsv).

--task readable names the variables of the readable table's functions (dread readable), after what
they share with the rest of the image: the globals they use, then the structures their variables
point to (where dread readable --check made a readable_types plan); --task globals asks for those
alone, for every function's readable C, checked or not, as the questions for function names show it,
which then show the names. Kept in the image's readable_names table; a run started again asks only
what has no name yet. After a --task name run, the globals' questions show the functions by their
proposals (names.tsv). On a build whose functions are all FUN_ (FreeRTOS, 2026-10-22: docs/todo.md),
globals named before the first pass made the function names worse, between it and --refine slightly
better.

A provider that declines DECLINED_IN_A_ROW questions in a row (stop reason "refusal" or
"content_filter") is asked no more in that run: its classifiers decline the same kind of question
again, and another provider may answer it. On 2026-10-20 Anthropic's Sonnet 5.5 and Opus 5.5 declined
every --task input question on FreeRTOS (a listing with instructions to reach), also with the purpose
stated, while Haiku 5.5 answered the same question.

    dread pilot [--target DIR] [--image NAME] [--provider NAME] [--tries N] [--limit N] [--no-lifted]
                [--no-mutate] [--no-integration] [--dry-run]
                [--task write|input|name|readable|globals|subsystems] [--jobs N] [--refine] [--behaviour]
                [--[no-]subsystems] [--samples K] [--temperature T] [--gate C] [--passes N] [--ablate]
                [--score TRUTH [--judge PROVIDER] [--upto-pass N] [--against NAMES_TSV]] [NAME...]
"""
import argparse
import collections
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
but not certain. A comment after a constant shows what it points to: text, a function (&name), or the
first words of a table (-> {...}: the texts and functions they point to). A name says what the function
does, in snake_case, with a prefix for its subsystem when one is clear (uart_, gpio_, timer_, fs_,
ui_); follow the style of the names you see. Answer with exactly two lines:

name: <the name>
what: <one sentence: what the function does>"""


SUBSYSTEM_SYSTEM = """You name the parts of a binary image. Tools grouped its functions from their code
alone: the memory, device registers and texts they share, the functions whose addresses one of them
takes, and their places in the image (compilers keep a source file's functions next to each other).
A group is most likely one source file or a few small neighbouring ones; the tools can be wrong at
its edges. The code below is C made from the machine code by tools (variables named after the
registers and stack slots that held them, memory at fixed addresses written g_ADDRESS, io_ADDRESS
for device registers, rom_ADDRESS in the image), or the machine code itself where no C could be made.
A function without a name is written FUN_ and its address; some names are proposals made from the
code earlier, likely but not certain. A comment after a constant shows what it points to. From all of
a group's evidence at once, say what part of the program it is: a short name ("uart driver", "file
system", "task scheduler", "C library stdio"), the prefix its functions' names would share (snake_case,
ending in an underscore: uart_, fs_, sched_, stdio_), and one sentence on what it does. Answer with
exactly three lines:

name: <the part's name>
prefix: <the prefix>
what: <one sentence: what the part does>"""

SUBSYSTEM_LINE = ("Tools grouped the program's functions from their code alone (what they share: memory, texts, "
                  "devices; neighbouring addresses), and a reading of all {size} functions of this one's group "
                  "named that part {name}, with the prefix {prefix} for its functions' names: {what}")


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


PROPOSED_FUNCTIONS = "The functions' names are proposals made from their code, likely but not certain."


def name_globals(target, image, program, provider, keys, uses, batch=40, shown_functions=4, out=print,
                 shown_as=None):
    """Ask for names of the memory at fixed addresses in keys that the readable_names table does not
    name yet, batch at a time, each with its uses in up to shown_functions functions; keep the names
    that are C identifiers no function or other global has. shown_as ({function: name shown}: a naming
    run's proposals) shows functions by those names, saying so (PROPOSED_FUNCTIONS). Returns how many
    were named."""
    from dread import naming
    table = naming.load(image, "globals")
    todo = [k for k in keys if k not in table and k in uses]
    taken = set(program.by_name) | {e.get("name") for e in table.values()} | naming.KEYWORDS | set(
        (shown_as or {}).values())
    folder = os.path.join(target.build, "pilot", image.name)
    os.makedirs(folder, exist_ok=True)

    def as_shown(text):
        return IDENT_WORD.sub(lambda m: shown_as.get(m.group(0), m.group(0)), text) if shown_as else text
    named = 0
    for start in range(0, len(todo), batch):
        part = todo[start:start + batch]
        lines = [PROPOSED_FUNCTIONS] if shown_as else []
        for key in part:
            u = uses[key]
            lines.append("%s (%s, in %d functions):" % (u["name"], key.replace(" ", ", "), len(u["functions"])))
            fns = []
            for fname, line in u["uses"]:
                if fname not in fns:
                    if len(fns) >= shown_functions:
                        continue
                    fns.append(fname)
                lines.append("    %s: %s" % (as_shown(fname), as_shown(line)))
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
            lines = [shapes.struct_text(k, plan.structs[k], pointers=plan.pointers.get(k))]
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


def name_shared(target, image, program, provider, context, lifter, names, everything, out=print, shown_as=None):
    """The names the functions of names share with the rest of the image, asked before their own: the
    globals they use (name_globals, with their uses in the readable C of everything; shown_as: the
    functions' proposed names), then the structures their variables point to (name_structs; only
    where the image has a readable_types plan, which dread readable --check makes). context takes the
    tables as they are then. Returns (globals named, structures named)."""
    from dread import naming
    every = sorted(set(everything) | set(names))
    uses, by_function = global_uses(program, context, lifter, every)
    wanted = sorted(set().union(*(by_function.get(n, set()) for n in names))) if names else []
    named = name_globals(target, image, program, provider, wanted, uses, out=out, shown_as=shown_as)
    context.globals = naming.load(image, "globals")
    structs = 0
    if context.plan.structs:                # (then the structures, which the variables' C shows)
        suses = struct_uses(program, context, lifter, every)
        swanted = sorted({k for n in names for k in context.plan.typed.get(n, {}).values()})
        taken = {e.get("name") for e in context.globals.values() if isinstance(e, dict)}
        structs = name_structs(target, image, program, provider, context.plan, suses, swanted, taken, out=out)
        context.structs, context._struct_names = naming.load(image, "structs"), None
    return named, structs


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
    lifter, the image's readable table, the callers of every function; with behaviour, what each
    function did in runs of its cases (dread.behaviour)."""

    def __init__(self, target, image, program, flow=None, behaviour=False):
        from dread import build, flat, lift, readable
        self.target, self.image, self.program, self.flow = target, image, program, flow
        self.context = readable.Context(target, image, program, flow)
        self.lifter = lift.make_lifter(program, flow)
        self.readable = set(build.read_names(image.table("readable")))
        self.callers = flat.callers_of(program)
        self.views, self.prepared, self.viewed = {}, set(), set()
        self.behaviour, self.runs, self._generator = behaviour, {}, None
        self.subsystem = {}                 # {function name: (its group's size, its subsystems.tsv row)}

    def prepare(self, names):
        """Make the readable C of names in a pool of workers (dread.readable views), and with behaviour
        their runs (dread.behaviour sections), before any thread asks: questions then only put names
        in."""
        self.prepare_code(names)
        if self.behaviour:
            from dread import behaviour
            self.runs.update(behaviour.sections(self.target, self.image, self.program, self.flow, list(names),
                                                called=set(self.callers)))
        self.prepared |= set(names)

    def prepare_code(self, names):
        """Make the readable C of names in a pool of workers (prepare() without the runs: for the
        questions about groups, which show code only)."""
        from dread import readable
        names = [n for n in names if n not in self.viewed]
        self.views.update(readable.views(self.program, self.context, self.lifter, names))
        self.viewed |= set(names)

    def runs_of(self, fn):
        """The behaviour section of a function (dread.behaviour), or None: without behaviour, or
        without a case that runs."""
        if not self.behaviour:
            return None
        if fn.name in self.prepared:
            return self.runs.get(fn.name)
        from dread import autocase, behaviour, naming
        if self._generator is None:
            self._generator = autocase.Generator(self.target, self.image, program=self.program)
        return behaviour.section(self._generator, fn, self.flow, naming.load(self.image, "globals"),
                                 has_callers=fn.name in self.callers)

    def code(self, fn):
        """(text, form) of a function's code for a question: its readable C ("readable" when the
        image's readable table has it, the gate having checked it; "readable, unchecked" else), or its
        listing ("listing") when it does not lift or its C is not the dialect."""
        from dread import flat, lift, readable
        if fn.name in self.viewed:
            text = self.views.get(fn.name)
        else:
            try:
                text = readable.view(fn.name, self.lifter.lift(fn).text, context=self.context)
            except lift.Refused:
                text = None
        if text is not None:
            return text, "readable" if fn.name in self.readable else "readable, unchecked"
        return "\n".join(flat.listing(self.program, fn, self.flow, callers=self.callers)), "listing"

    def annotated(self, fn, shown_as=None):
        """(text, form) of a function's code as the questions show it: code() with each constant
        followed by what it points to (dread.constants pointed_note: text, a function, a table of
        texts and functions) and the other functions by their names in shown_as ({name: name shown})."""
        from dread.constants import pointed_note
        program = self.program
        text, form = self.code(fn)
        if form != "listing":               # (the listing shows the texts already)
            def note(m):
                s = pointed_note(program, int(m.group(1), 16))
                return m.group(0) + (" /* %s */" % s.replace("*/", "* /") if s else "")
            text = HEX.sub(note, text)
        return self.named(fn, text, shown_as), form

    @staticmethod
    def named(fn, text, shown_as):
        """text with the other functions' names replaced by those of shown_as."""
        if not shown_as:
            return text
        return IDENT_WORD.sub(lambda m: shown_as.get(m.group(0), m.group(0)) if m.group(0) != fn.name
                              else m.group(0), text)

    def question(self, fn, shown_as=None, shuffle=None):
        """(text, form) of the question for a function's name: its code (annotated(): each constant
        followed by what it points to, other functions by their names in shown_as, {name: name shown}:
        the run's proposals) and its callers; with behaviour, what it did in runs of its cases (runs_of),
        the form then ending in BEHAVIOUR; with its group named (subsystem, --subsystems), the group's
        name, prefix and sentence (SUBSYSTEM_LINE), the form then ending in SUBSYSTEM; shuffle, a seed:
        the callers in an order of their own (random.Random(shuffle), the same for the same seed), for
        a vote's samples (the runs shown as they are)."""
        text, form = self.annotated(fn, shown_as)
        runs = self.runs_of(fn)

        def named(text):
            return self.named(fn, text, shown_as)
        if len(text) > NAME_CHARS:
            text = text[:NAME_CHARS] + "\n... (cut: %d more characters)" % (len(text) - NAME_CHARS)
        if form == "listing":
            user = "The function %s, its machine code (no C could be made):\n\n```\n%s\n```" % (fn.name, text)
        else:
            user = "The function %s, as C made from its machine code:\n\n```c\n%s\n```" % (fn.name, text)
            callers = []
            for caller in self.caller_names(fn):
                shown = (shown_as or {}).get(caller, caller)
                if shown not in callers:
                    callers.append(shown)
            if shuffle is not None:
                import random
                random.Random(shuffle).shuffle(callers)
            if callers:
                more = " and %d more" % (len(callers) - 12) if len(callers) > 12 else ""
                user += "\n\nIt is called by: %s%s." % (", ".join(callers[:12]), more)
        if runs:
            user += "\n\n" + named(runs)
            form += BEHAVIOUR
        if fn.name in self.subsystem:
            size, row = self.subsystem[fn.name]
            user += "\n\n" + SUBSYSTEM_LINE.format(size=size, name=row["name"], prefix=row["prefix"], what=row["what"])
            form += SUBSYSTEM
        return user, form

    def caller_names(self, fn):
        """The functions that call fn directly that an LLM may be shown, each once, in call order."""
        out = []
        for caller, _ in self.callers.get(fn.name, []):
            if caller not in out and may_show(self.target, caller):
                out.append(caller)
        return out

    def names_shown(self, fn, shown_as):
        """The other functions whose names in shown_as fn's question shows: those its code and its runs
        (with behaviour) mention and, below readable C, its callers (a code cut at NAME_CHARS may show
        fewer)."""
        if not shown_as:
            return set()
        text, form = self.code(fn)
        text += "\n" + (self.runs_of(fn) or "")
        found = {w for w in IDENT_WORD.findall(text) if w in shown_as and w != fn.name}
        if form != "listing":
            found |= {c for c in self.caller_names(fn) if c in shown_as}
        return found


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


def ask_name(provider, program, name, user, tries=2, temperature=None):
    """(proposed, what, log, why) of asking for one function's name: a name that cannot be used
    (name_problem) or an answer without the two lines goes back with the reason, up to tries
    answers; proposed is None when none came. temperature: the question's sampling temperature
    (llm.Provider), None for the provider's own."""
    messages = [{"role": "user", "content": user}]
    log = ["# %s, asked %s\n\n## Question\n\n%s" % (name, provider.label(), user)]
    why = "no answer"
    for _ in range(tries):
        provider.last = {}
        try:
            answer = provider.ask(NAME_SYSTEM, messages) if temperature is None else \
                provider.ask(NAME_SYSTEM, messages, temperature=temperature)
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


# names.tsv's columns (docs/formats.md); a file of 2026-10-22 or older has the first six
NAMES_COLUMNS = ("address", "now", "proposed", "what", "provider", "shown", "confidence", "samples", "pass",
                 "question", "names_shown", "hidden", "depends")
REFINED = ", refined"       # names.tsv's shown column of a second pass's proposal (--refine) ...
BEHAVIOUR = ", behaviour"   # ... of a question that showed the function's runs (--behaviour) ...
SUBSYSTEM = ", subsystem"   # ... and of one that showed its group's name and prefix (--subsystems)


def marked(shown, mark):
    """Whether names.tsv's shown column has a mark (REFINED, BEHAVIOUR), wherever it stands among
    them ("readable, behaviour, refined")."""
    return mark.strip(", ") in [part.strip() for part in (shown or "").split(",")]
REFINE_NOTE = ("A first reading of this function's code alone, before its callers had names, named it "
               "{name}: {what} Its callers have names now. Keep that name if they confirm it, or give a better one.")


def read_name_rows(path, upto_pass=None):
    """The rows of a names.tsv, in file order, as {column: text} by its header's columns, with "entry"
    the address as a number: an older file's missing columns read as empty and its pass as 1 (2 for
    a row --refine wrote); rows without an address or a proposal are left out. upto_pass: only the
    rows of passes up to it (the proposals as they were after that pass)."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        lines = f.read().splitlines()
    header = lines[0].split("\t") if lines else []
    for line in lines[1:]:
        row = dict(zip(header, line.split("\t")))
        for k in NAMES_COLUMNS:
            row.setdefault(k, "")
        try:
            row["entry"] = int(row["address"], 16)
        except ValueError:
            continue
        if not row["proposed"]:
            continue
        if not row["pass"].isdigit():
            row["pass"] = "2" if marked(row["shown"], REFINED) else "1"
        if upto_pass is None or int(row["pass"]) <= upto_pass:
            rows.append(row)
    return rows


def last_rows(rows):
    """{entry: row} of the last row of each address."""
    return {r["entry"]: r for r in rows}


def confidence(row):
    """A names.tsv row's confidence as a number (a vote's agreement over its questions), or None (one
    question's proposal, an older file)."""
    try:
        return float(row.get("confidence") or "")
    except ValueError:
        return None


def read_proposals(target, image, refined=None, upto_pass=None, path=None):
    """{address: (proposed, what)} of the image's names.tsv (or of the names.tsv at path: another
    run's), the last proposal of each address (of the passes up to upto_pass); refined, a set: gets
    the addresses whose last proposal came from a second pass (--refine)."""
    out = {}
    for row in read_name_rows(path or names_path(target, image), upto_pass):
        out[row["entry"]] = (row["proposed"], row["what"])
        if refined is not None:
            if marked(row["shown"], REFINED):
                refined.add(row["entry"])
            else:
                refined.discard(row["entry"])
    return out


def keep_proposal(target, image, fn, proposed, what, provider, form, more=None):
    """A line of the image's names.tsv (NAMES_COLUMNS): address, now, proposed, what, provider,
    shown (the form of the code the question showed), and more's columns (a vote's confidence and
    samples, the pass, the question's digest, ...), empty where not given. A file with an older
    header gets the new one first; its rows stay as they are (their missing columns read as empty)."""
    path = names_path(target, image)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    header = "\t".join(NAMES_COLUMNS) + "\n"
    if os.path.exists(path):
        with open(path) as f:
            first = f.readline()
        if first != header:
            with open(path) as f:
                rest = f.readlines()[1:]
            with open(path, "w") as f:
                f.write(header + "".join(rest))
    else:
        with open(path, "w") as f:
            f.write(header)
    values = dict(more or {})
    values.update({"address": "0x%x" % fn.entry, "now": fn.name, "proposed": proposed,
                   "what": " ".join(what.split()), "provider": provider.label(), "shown": form})
    row = {k: str(values.get(k, "")) for k in NAMES_COLUMNS}
    with open(path, "a") as f:
        f.write("\t".join(row[k] for k in NAMES_COLUMNS) + "\n")
    return row


def propose_name(target, image, program, provider, name, flow=None, out=print, namer=None, shown_as=None,
                 tries=2, behaviour=False):
    """Ask for one function's name and a sentence; a proposal goes to BUILD/pilot/IMAGE/names.tsv,
    never into the tables (no tool can check a name), the conversation to
    BUILD/pilot/IMAGE/names/NAME.md. Returns (name, what) or None. behaviour: the question shows
    what the function did in runs of its cases (Namer)."""
    namer = namer or Namer(target, image, program, flow, behaviour)
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


def call_components(program, names):
    """(component of each name, members of each component, the components each calls) of the call
    graph among names (direct calls and tail calls): functions that call each other in a circle are
    one component; components are numbered so that one comes after every component it calls."""
    from dread.lift import direct_callees
    nameset, position = set(names), {n: k for k, n in enumerate(names)}
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
                    comps.append(sorted(members, key=position.get))
                if work:
                    u = work[-1][0]
                    low[u] = min(low[u], low[v])
    callees = [sorted({comp[w] for m in members for w in calls[m]} - {c}) for c, members in enumerate(comps)]
    return comp, comps, callees


def callees_first(program, names):
    """The functions of names in rounds, as lists: a function's round comes after those of the
    functions it calls among names (direct calls and tail calls), functions that call each other in a
    circle sharing one round, so that a question can show what its callees were named."""
    comp, comps, callees = call_components(program, names)
    level = {}
    for c in range(len(comps)):             # (a component comes after those it calls)
        level[c] = 1 + max([level[d] for d in callees[c]] or [-1])
    rounds = [[] for _ in range(1 + max(level.values(), default=-1))]
    for n in names:
        rounds[level[comp[n]]].append(n)
    return rounds


# ---- subsystems: the groups of dread.groups, named from all of their evidence at once

SUBSYSTEM_COLUMNS = ("group", "first", "functions", "members", "name", "prefix", "what", "provider", "confidence",
                     "samples", "question")
GROUP_COLUMNS = ("address", "now", "group", "members")
SUBSYSTEM_CHARS = 24000     # the code a group's question shows at most ...
SUBSYSTEM_EACH = 1500       # ... and of each function at least this much while the whole fits
SUBSYSTEM_TEXTS = 40        # the texts a group's question lists at most
SUBSYSTEM_LISTED = 12       # the functions outside the group it lists as callers, and as callees
SUBSYSTEM_SMALLEST = 2      # a group of fewer functions is not asked about: its one function says all


def subsystems_path(target, image):
    return os.path.join(target.build, "pilot", image.name, "subsystems.tsv")


def groups_path(target, image):
    return os.path.join(target.build, "pilot", image.name, "groups.tsv")


def members_key(program, group):
    """A digest of a group's functions (their entries): a subsystems.tsv row belongs to the group
    whose functions are these, whatever its number in a later grouping."""
    import hashlib
    return hashlib.sha1(" ".join("%x" % e for e in sorted(program.by_name[n].entry for n in group)).encode()
                        ).hexdigest()[:12]


def read_table(path, columns):
    """The rows of a TSV with a header line as {column: text}, the columns a file lacks empty."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = f.read().splitlines()
    header = lines[0].split("\t") if lines else []
    rows = []
    for line in lines[1:]:
        row = dict(zip(header, line.split("\t")))
        for k in columns:
            row.setdefault(k, "")
        rows.append(row)
    return rows


def read_subsystems(path):
    """{members key: the last subsystems.tsv row of that group}."""
    return {r["members"]: r for r in read_table(path, SUBSYSTEM_COLUMNS) if r["members"] and r["prefix"]}


def write_groups(target, image, program, found):
    """groups.tsv: each function's group of this grouping (found: dread.groups find), its number and
    its members key."""
    path = groups_path(target, image)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\t".join(GROUP_COLUMNS) + "\n")
        for k, group in enumerate(found):
            key = members_key(program, group)
            for n in group:
                f.write("0x%x\t%s\t%d\t%s\n" % (program.by_name[n].entry, n, k, key))


def function_subsystems(target, image):
    """{entry address: (its group's size, its subsystems.tsv row)} of the image's groups.tsv and
    subsystems.tsv: the part each function was found in and its name, where it has one."""
    rows = read_subsystems(subsystems_path(target, image))
    members = read_table(groups_path(target, image), GROUP_COLUMNS)
    sizes = collections.Counter(r["members"] for r in members)
    out = {}
    for r in members:
        if r["members"] in rows:
            try:
                out[int(r["address"], 16)] = (sizes[r["members"]], rows[r["members"]])
            except ValueError:
                continue
    return out


def keep_subsystem(target, image, values):
    """A line of subsystems.tsv (SUBSYSTEM_COLUMNS) from values, empty where not given."""
    path = subsystems_path(target, image)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write("\t".join(SUBSYSTEM_COLUMNS) + "\n")
    row = {k: " ".join(str(values.get(k, "")).split()) for k in SUBSYSTEM_COLUMNS}
    with open(path, "a") as f:
        f.write("\t".join(row[k] for k in SUBSYSTEM_COLUMNS) + "\n")
    return row


def subsystem_question(namer, group, touched, shown_as=None):
    """The question for a group's name (group: function names in address order; touched: dread.groups
    evidence): its size and place, the names of its functions that have one (their own or shown_as's
    proposal), the texts they point to (the most used first, SUBSYSTEM_TEXTS at most), the devices
    whose registers they use, the functions outside the group that call them and that they call
    (SUBSYSTEM_LISTED each), and their code (Namer.annotated), each cut to its share of SUBSYSTEM_CHARS
    and at least SUBSYSTEM_EACH while the whole fits; the functions left out for room are named."""
    from dread import constants, groups as grouping
    from dread.samecode import UNNAMED
    program, target = namer.program, namer.target
    shown_as = shown_as or {}
    members = [n for n in group if may_show(target, n)]
    inside = set(group)
    first, last = program.by_name[group[0]], program.by_name[group[-1]]
    lines = ["The group of %d functions at 0x%x-0x%x (of %d functions in the image)." % (
        len(group), first.entry, last.entry, len(program.functions))]
    names = [shown_as.get(n, n) for n in members]
    named = [x for x in names if not UNNAMED.match(x)]
    if named:
        lines.append("Its functions' names (some proposed from their code): %s." % ", ".join(named))
    texts = collections.Counter()
    devices = collections.Counter()
    for n in members:
        for kind, key in touched.get(n, ()):
            if kind == "text":
                texts[key] += 1
            elif kind == "device":
                devices[key] += 1
    if texts:
        shown = []
        for address, _ in sorted(texts.items(), key=lambda kv: (-kv[1], kv[0]))[:SUBSYSTEM_TEXTS]:
            text = constants.string_at(program, address, limit=64)
            if text:
                shown.append('"%s"' % text)
        more = " and %d more" % (len(texts) - SUBSYSTEM_TEXTS) if len(texts) > SUBSYSTEM_TEXTS else ""
        lines.append("Texts its functions point to: %s%s." % (", ".join(shown), more))
    if devices:
        lines.append("Device registers its functions use: %s." % ", ".join(
            "0x%x-0x%x (%d functions)" % (b * grouping.DEVICE_BLOCK, (b + 1) * grouping.DEVICE_BLOCK - 1, k)
            for b, k in sorted(devices.items())))
    callers, callees = [], []
    for n in members:
        for caller, _ in namer.callers.get(n, []):
            if caller not in inside and may_show(target, caller) and shown_as.get(caller, caller) not in callers:
                callers.append(shown_as.get(caller, caller))
    for callee, sites in sorted(namer.callers.items()):
        if callee not in inside and may_show(target, callee) and any(c in inside for c, _ in sites):
            callees.append(shown_as.get(callee, callee))
    for label, found in (("Functions outside the group that call its functions", callers),
                         ("Functions outside the group that its functions call", callees)):
        if found:
            more = " and %d more" % (len(found) - SUBSYSTEM_LISTED) if len(found) > SUBSYSTEM_LISTED else ""
            lines.append("%s: %s%s." % (label, ", ".join(found[:SUBSYSTEM_LISTED]), more))
    each = max(SUBSYSTEM_EACH, SUBSYSTEM_CHARS // max(1, len(members)))
    parts, left, used = [], [], 0
    for n in members:
        text, form = namer.annotated(program.by_name[n], shown_as)
        if len(text) > each:
            text = text[:each] + "\n... (cut: %d more characters)" % (len(text) - each)
        if used + len(text) > SUBSYSTEM_CHARS:
            left.append(shown_as.get(n, n))
            continue
        used += len(text)
        parts.append(("// %s: its machine code (no C could be made)\n" % n if form == "listing" else "") + text)
    lines.append("Its functions, as C made from their machine code:\n\n```c\n%s\n```" % "\n\n".join(parts))
    if left:
        lines.append("Not shown for room: %s." % ", ".join(left))
    return "\n\n".join(lines)


def parse_subsystem(answer):
    """(name, prefix, what) of an answer's three lines, or None; a prefix without its underscore gets
    one."""
    def line(key, pattern=r"(.+?)"):
        m = re.search(r"^\s*\**%s\**\s*:\s*\**`?%s`?\**\s*$" % (key, pattern), answer or "", re.M | re.I)
        return m.group(1).strip() if m else None
    name, prefix, what = line("name"), line("prefix", r"([A-Za-z_][A-Za-z0-9_]*)"), line("what")
    if not name or not prefix or not what:
        return None
    return name, prefix if prefix.endswith("_") else prefix + "_", what


def subsystem_problem(prefix):
    """Why a proposed prefix cannot be used, or None: an identifier's start of at most 24 characters."""
    if not re.match(r"^[A-Za-z][A-Za-z0-9_]{0,22}_$", prefix or ""):
        return "a prefix is a C identifier's start of at most 24 characters ending in an underscore"
    return None


def ask_subsystem(provider, user, tries=2, temperature=None):
    """((name, prefix, what) or None, log, why) of asking for one group's name: an answer without the
    three lines or with a prefix that cannot be used goes back with the reason, up to tries answers."""
    messages = [{"role": "user", "content": user}]
    log = ["## Question, asked %s\n\n%s" % (provider.label(), user)]
    why = "no answer"
    for _ in range(tries):
        provider.last = {}
        try:
            answer = provider.ask(SUBSYSTEM_SYSTEM, messages) if temperature is None else \
                provider.ask(SUBSYSTEM_SYSTEM, messages, temperature=temperature)
        except Exception as e:
            return None, log, "the provider: %s" % e
        log.append("## Answer\n\n%s" % answer)
        if (getattr(provider, "last", {}) or {}).get("finish") in llm.Provider.REFUSED:
            return None, log, "the provider declined"
        parsed = parse_subsystem(answer)
        why = "no three lines name:, prefix: and what:" if parsed is None else subsystem_problem(parsed[1])
        if why is None:
            return parsed, log, None
        messages += [{"role": "assistant", "content": answer},
                     {"role": "user", "content": "That cannot be used: %s. Answer again with the three lines." % why}]
        log.append("## Back to the model\n\n%s" % messages[-1]["content"])
    return None, log, why


def name_subsystems(target, image, program, provider, found, namer, touched, samples=1, jobs=4, tries=2,
                    temperature=None, shown_as=None, out=print):
    """Names for the groups of found (dread.groups find, numbered by their place) that subsystems.tsv
    does not name yet (by their members key) and that have SUBSYSTEM_SMALLEST functions or more: each
    asked samples times (subsystem_question, the same question each time: the provider's sampling
    varies the answers), jobs at a time, provider() giving each thread its provider; the prefix most
    answers agree with is kept (dread.naming vote), with the name and the sentence of the answer that
    gave it, the agreement its confidence. groups.tsv gets the grouping, subsystems.tsv a line a
    group, the conversations go to BUILD/pilot/IMAGE/subsystems/. Returns {function name: (its
    group's size, its row)} of the named groups."""
    import concurrent.futures
    from dread import naming
    write_groups(target, image, program, found)
    known = read_subsystems(subsystems_path(target, image))
    todo = []
    for k, group in enumerate(found):
        key = members_key(program, group)
        if key not in known and len(group) >= SUBSYSTEM_SMALLEST and any(may_show(target, n) for n in group):
            todo.append((k, group, key, subsystem_question(namer, group, touched, shown_as)))

    def one(item):
        k, sample, user = item
        p = provider()
        got, log, why = ask_subsystem(p, user, tries, temperature)
        return k, sample, got, log, why, p
    results = {}
    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            for k, sample, got, log, why, p in pool.map(one, [(k, j, user) for k, _, _, user in todo
                                                              for j in range(max(1, samples))]):
                results.setdefault(k, {})[sample] = (got, log, why, p)
    folder = os.path.join(target.build, "pilot", image.name, "subsystems")
    for k, group, key, user in todo:
        got = [results[k][j] for j in range(max(1, samples))]
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "group-%d.md" % k), "w") as f:
            f.write("# Group %d (%s)\n\n%s\n" % (k, key, "\n\n".join(
                ("# Question %d of %d\n\n" % (j + 1, len(got)) if len(got) > 1 else "") + "\n\n".join(r[1])
                for j, r in enumerate(got))))
        voted = naming.vote([r[0][1] if r[0] else None for r in got])
        if voted is None:
            out("group %d (%d functions): no proposal: %s" % (k, len(group), next((r[2] for r in got if r[2]),
                                                                                  "no answer")))
            continue
        prefix, agreement, index = voted
        name, _, what = got[index][0]
        values = {"group": k, "first": "0x%x" % program.by_name[group[0]].entry, "functions": len(group),
                  "members": key, "name": name, "prefix": prefix, "what": what, "provider": got[index][3].label(),
                  "question": question_key(user)}
        if len(got) > 1:
            values["confidence"] = "%.2f" % (agreement / len(got))
            values["samples"] = " ".join(r[0][1] if r[0] else "-" for r in got)
        known[key] = keep_subsystem(target, image, values)
        out("group %d (%d functions): %s, prefix %s%s: %s" % (
            k, len(group), name, prefix, " (%d of %d agree)" % (agreement, len(got)) if len(got) > 1 else "", what))
    return {n: (len(group), known[members_key(program, group)]) for group in found
            if members_key(program, group) in known for n in group}


def uses_prefix(name, prefix):
    """Whether a function's name begins with a group's prefix, piece by piece (dread.naming pieces:
    queue_send and QueueSend with queue_, not queues_send)."""
    from dread.naming import pieces
    want = pieces(prefix)
    return bool(want) and pieces(name)[:len(want)] == want


SUBSYSTEM_JUDGE = """You compare names given to parts of a program with the source files they come from.
On each numbered line, first the source file most of a part's functions come from (its path's end),
then the name and the prefix proposed for the part from its code. Say for each line whether the
proposal names what that file is: "same" (the same part of the program, in other words too), "close"
(related: more general, more specific, or one side of it), or "different". Answer with one ```json
block that maps every line's number to its word:

```json
{"1": "same", "2": "close", "3": "different"}
```"""


def score_subsystems(target, image, truth_path, rows, out=print, judge=None):
    """The groups (groups.tsv, subsystems.tsv) against the run's proposals (rows: {entry: names.tsv
    row}) and, where the truth is an ELF with debug information, against the source files: the share
    of proposals in a named group that begin with its prefix (uses_prefix: the names' consistency),
    and per group its most common file, the share of its functions from it, its name and prefix, and
    with judge a verdict whether they name that file's part (SUBSYSTEM_JUDGE). A line a group into
    BUILD/pilot/IMAGE/names-subsystems.tsv. Returns the totals ({} without a named group)."""
    from dread import groups as grouping
    parts = function_subsystems(target, image)
    if not parts:
        return {}
    inside = [(e, parts[e][1]) for e in rows if e in parts]
    using = sum(uses_prefix(rows[e]["proposed"], r["prefix"]) for e, r in inside)
    t = {"proposals": len(inside), "use_prefix": using}
    out("dread pilot --score: %d of %d proposals in a named group begin with its prefix (%.0f %%)" % (
        using, len(inside), 100.0 * using / len(inside) if inside else 0))
    files = {}
    try:
        files = grouping.source_files(truth_path)
    except Exception:                       # (a functions table, or no debug information to read)
        files = {}
    groups = {}
    for e, (size, row) in sorted(parts.items()):
        groups.setdefault(row["members"], (row, []))[1].append(e)
    lines = []
    for key, (row, entries) in sorted(groups.items(), key=lambda kv: int(kv[1][0]["group"] or 0)):
        c = collections.Counter(os.path.basename(files[e]) for e in entries if e in files)
        top, n = c.most_common(1)[0] if c else ("", 0)
        here = [e for e in entries if e in rows]
        lines.append({"group": row["group"], "functions": len(entries), "file": top, "from_file": n,
                      "name": row["name"], "prefix": row["prefix"],
                      "use_prefix": sum(uses_prefix(rows[e]["proposed"], row["prefix"]) for e in here),
                      "proposals": len(here)})
    verdicts = {}
    if judge is not None and files:
        asked = [(k, line["file"], "%s (prefix %s)" % (line["name"], line["prefix"])) for k, line in enumerate(lines)
                 if line["file"]]
        verdicts = judge_names(judge, asked, out=out, system=SUBSYSTEM_JUDGE)
    path = os.path.join(target.build, "pilot", image.name, "names-subsystems.tsv")
    with open(path, "w") as f:
        f.write("group\tfunctions\tname\tprefix\tfile\tfrom_file\tproposals\tuse_prefix\tjudged\n")
        for k, line in enumerate(lines):
            f.write("%s\t%d\t%s\t%s\t%s\t%d\t%d\t%d\t%s\n" % (
                line["group"], line["functions"], line["name"], line["prefix"], line["file"], line["from_file"],
                line["proposals"], line["use_prefix"], verdicts.get(k, "")))
    if files:
        out("dread pilot --score: %d named groups, %d of their %d functions from their most common file (%s)" % (
            len(lines), sum(x["from_file"] for x in lines), sum(x["functions"] for x in lines), path))
    if verdicts:
        counts = collections.Counter(verdicts.values())
        out("dread pilot --score: the groups' names against their files, judged by %s: the same %d, close %d, "
            "different %d" % (judge.label(), counts["same"], counts["close"], counts["different"]))
        t.update({"judged_" + v: counts[v] for v in VERDICTS})
    t["groups"] = lines
    return t


NAME_SAMPLES = 5    # --task name's default questions per function (--samples) and passes (--passes): measured
NAME_PASSES = 2     # on FreeRTOS against one question in one pass (docs/decisions.md, 2026-10-22)
NAME_SUBSYSTEMS = True  # --task name names the groups first and shows them (--subsystems): measured on FreeRTOS
                        # against the two defaults above (docs/decisions.md, 2026-10-22)


def question_key(text):
    """A digest of a question's text (names.tsv's question column): a later pass asks a function again
    only when its question changed."""
    import hashlib
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def name_functions(target, image, program, make_provider, names, flow=None, jobs=4, tries=2, out=print,
                   refine=False, behaviour=False, samples=1, temperature=None, gate=None, passes=1,
                   ablate=False, subsystems=False):
    """Proposals for the functions of names, callees first: a function is asked as soon as every
    function it calls among names has its answer (call_components; functions that call each other in
    a circle together), jobs questions at a time with a provider each (make_provider() makes one), each
    question showing the proposals made so far for the other functions in place of their names. A
    function names.tsv has a proposal for already is not asked again, its proposal shown. Returns
    (proposed, asked, spent: summed over the providers), proposals and functions asked counted over
    the passes. (Rounds with a barrier each ran at a fifth of the speed once the rounds got small:
    each waited for its slowest answer.)

    samples: each function asked so many times (self-consistency), the first question as it stands,
    the others with its callers in an order of their own (Namer.question's shuffle; the variation
    is the provider's sampling: Anthropic's models sample at their default temperature 1.0, which is
    the only one Haiku 5.5 and Sonnet 5.5 accept; temperature, for every question, where a provider
    takes one). The names go to dread.naming.vote: the name most samples agree with is kept, its
    agreement over samples its confidence (names.tsv's confidence, and its samples column).

    gate: a question shows only proposals whose confidence is at least gate (the others stay FUN_);
    a proposal without one (a single question's, an older file's) is not shown. passes: after the
    first pass (callees first, the functions without a proposal), passes 2, 3 ... (callers first on
    even passes, callees first on odd ones) ask a function again when its question changed since its
    last proposal (names.tsv's question column: a digest), its question showing no proposal of its
    own; its last proposal runs in the vote as a candidate that wins ties (a name stays while this
    pass's samples agree with it as much as with any of theirs). The passes end when a pass changes
    no name a question shows (none would ask anything), or at passes. A line per pass says what it
    asked and changed; every proposal is kept with its pass. A run started again goes through its
    passes again, each asking only what has no proposal or changed.

    ablate: each function is asked samples times more with no other function's proposal shown (its
    callers and callees by their own names); the vote of those is names.tsv's hidden column, and
    depends says whether it disagrees with the proposal (dread.naming.similarity below AGREE): a name
    that depends on the names shown. Where a question shows no proposal (names_shown 0) both
    questions are the same: their disagreement measures the votes' own noise. A function is asked so
    once; later passes compare with its hidden name.

    refine: a second pass, callers first: every function with a first proposal is asked again once
    its callers have their second answers, its question showing its callers' names and its first name
    (REFINE_NOTE); its answer is kept marked REFINED, and a run started again skips what is refined.

    behaviour: each question also shows what the function did in runs of its cases (dread.behaviour,
    made for all of them before the first question).

    subsystems: before the first question the image's functions are grouped by their code (dread.groups)
    and each group not named yet is asked for its name, prefix and sentence (name_subsystems, samples
    questions each, the proposals so far shown), and each function's question then shows its group's
    (SUBSYSTEM_LINE)."""
    import concurrent.futures
    import threading
    from dread import naming
    namer = Namer(target, image, program, flow, behaviour)
    samples = max(1, int(samples))
    state = last_rows(read_name_rows(names_path(target, image)))     # {entry: the last row}
    refined = {e for e, r in state.items() if marked(r["shown"], REFINED)}

    def shown_name(row):
        """The name a question shows for the function of this row: its proposal, unless a gate hides
        it."""
        if row is None or (gate and (confidence(row) is None or confidence(row) < gate)):
            return None
        return row["proposed"]
    shown_as = {}
    for e, row in state.items():
        if e in program.by_entry and shown_name(row) is not None:
            shown_as[program.by_entry[e].name] = shown_name(row)
    todo = [n for n in names if refine or passes > 1 or program.by_name[n].entry not in state]
    started = time.time()
    namer.prepare(todo)                     # (before any thread: the samples' questions only put names in)
    if behaviour and todo:
        out("dread pilot: runs of their cases for %d of %d functions (dread.behaviour, %.0f s)" % (
            len([n for n in todo if namer.runs.get(n)]), len(todo), time.time() - started))
    comp, comps, callees = call_components(program, list(names))
    callers = [[] for _ in comps]
    for c, cs in enumerate(callees):
        for d in cs:
            callers[d].append(c)
    local, providers, lock = threading.local(), [], threading.Lock()
    run = {"proposed": 0, "asked": 0, "declined": 0, "failed": 0, "stopped": False}

    def provider():
        if getattr(local, "provider", None) is None:
            local.provider = make_provider()
            with lock:
                providers.append(local.provider)
        return local.provider

    if subsystems:
        from dread import groups as grouping
        started = time.time()
        try:
            touched = grouping.evidence(target, image, program, flow)
            found = grouping.find(target, image, program, flow, touched=touched)
        except Exception as e:              # (the names do without: said, not fatal)
            out("dread pilot: no subsystems, the grouping failed: %s: %s" % (type(e).__name__, e))
            found = None
        if found is not None:
            namer.prepare_code([fn.name for fn in program.functions if may_show(target, fn.name)])     # (the
            # groups' questions show every function's code)
            out("dread pilot: %d functions in %d groups (dread.groups, %.0f s)" % (len(program.functions), len(found),
                                                                                  time.time() - started))
            namer.subsystem = name_subsystems(target, image, program, provider, found, namer, touched, samples, jobs,
                                              tries, temperature, shown_as, out)

    def one(item):
        name, kind, k, user = item
        p = provider()
        proposed, what, log, why = ask_name(p, program, name, user, tries, temperature)
        return name, kind, k, proposed, what, log, why, p, (getattr(p, "last", {}) or {}).get("finish")

    def settle(name, job, number, kind):
        """Vote over a function's answers, keep the proposal, update what questions show; returns
        whether the name its callers' and callees' questions show changed (None: no proposal)."""
        fn = job["fn"]
        results = job["results"]
        log = []
        for (qkind, k) in sorted(results, key=lambda q: (q[0] != "shown", q[1])):
            if len(results) > 1:
                log.append("# Question %d of %d%s" % (k + 1, samples, ", no other function's proposal shown"
                                                      if qkind == "hidden" else ""))
            log += results[(qkind, k)][2]
        write_log(target, image, name, log)
        shown = [results.get(("shown", k)) for k in range(samples)]
        got = [r[0] if r else None for r in shown]
        old = state.get(fn.entry)
        voted = naming.vote(got, old["proposed"] if old is not None and kind == "again" else None)
        if voted is None:
            out("%-24s no proposal: %s" % (name, next((r[3] for r in shown if r), "no answer")))
            return None
        proposed, agreement, index = voted
        if index is None:                   # (the name stays: the sentence of the sample most like it)
            index = max((k for k in range(samples) if got[k]), key=lambda k: (naming.similarity(proposed, got[k]), -k))
        what, p = shown[index][1], shown[index][4]
        more = {"pass": str(number), "question": job["key"], "names_shown": str(len(job["names"]))}
        if samples > 1:
            more["confidence"] = "%.2f" % (agreement / samples)
            more["samples"] = " ".join(g or "-" for g in got)
        hidden = None
        if any(q[0] == "hidden" for q in results):
            voted = naming.vote([results[("hidden", k)][0] if ("hidden", k) in results else None
                                 for k in range(samples)])
            hidden = voted[0] if voted else None
        elif old is not None and old.get("hidden"):
            hidden = old["hidden"]
        if hidden:
            more["hidden"] = hidden
            more["depends"] = "no" if naming.similarity(proposed, hidden) >= naming.AGREE else "yes"
        row = keep_proposal(target, image, fn, proposed, what, p, job["form"], more)
        row["entry"] = fn.entry
        state[fn.entry] = row
        if kind == "refine":
            refined.add(fn.entry)
        before, now = shown_as.get(name), shown_name(row)
        if now is None:
            shown_as.pop(name, None)
        else:
            shown_as[name] = now
        notes = [job["form"]]
        if samples > 1:
            notes.append("%d of %d agree" % (agreement, samples))
        if more.get("depends") == "yes":
            notes.append("alone: %s" % hidden)
        out("%-24s proposed %s (%s): %s" % (name, proposed, ", ".join(notes), what))
        return before != now

    def wanted(kind, row, key):
        """Whether a pass asks about a function with this last row and this question now: the first
        pass what has no proposal, --refine what has one not refined yet, a later pass what has none or
        whose question changed since."""
        if kind == "first":
            return row is None
        if kind == "refine":
            return row is not None and row["entry"] not in refined
        return row is None or row["question"] != key

    def one_pass(number, kind):
        """kind: "first" (callees first, the functions without a proposal), "again" (callers first on
        even passes: the functions whose question changed), "refine". Returns (proposed, asked,
        changed): changed, the functions whose shown name changed."""
        down = kind == "refine" or (kind == "again" and number % 2 == 0)       # (callers first)
        deps, then = (callers, callees) if down else (callees, callers)
        waiting = [len(d) for d in deps]
        left = [0] * len(comps)
        ready = [c for c in range(len(comps)) if not waiting[c]]
        jobs_of, counts = {}, {"proposed": 0, "asked": 0, "changed": 0}

        def finished(c):                    # (the components that wait for it may be ready now)
            for d in then[c]:
                waiting[d] -= 1
                if not waiting[d]:
                    ready.append(d)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            running = {}
            while (ready and not run["stopped"]) or running:
                while ready and not run["stopped"]:
                    c = ready.pop(0)
                    todo = []
                    for n in comps[c]:
                        fn = program.by_name[n]
                        user, form = namer.question(fn, shown_as)
                        key = question_key(user)
                        if wanted(kind, state.get(fn.entry), key):
                            todo.append((n, fn, user, form, key))
                    left[c] = len(todo)
                    if not todo:
                        finished(c)
                    for n, fn, user, form, key in todo:
                        note = ""
                        if kind == "refine":
                            note = "\n\n" + REFINE_NOTE.format(name=state[fn.entry]["proposed"],
                                                               what=state[fn.entry]["what"])
                            form += REFINED
                        seed = ["%x:%d" % (fn.entry, k) if k else None for k in range(samples)]
                        questions = [("shown", 0, user + note)] + [
                            ("shown", k, namer.question(fn, shown_as, shuffle=seed[k])[0] + note)
                            for k in range(1, samples)]
                        if ablate and not (state.get(fn.entry) or {}).get("hidden"):
                            questions += [("hidden", k, namer.question(fn, None, shuffle=seed[k])[0] + note)
                                          for k in range(samples)]
                        jobs_of[n] = {"fn": fn, "form": form, "key": key, "component": c, "results": {},
                                      "questions": len(questions), "names": namer.names_shown(fn, shown_as)}
                        for q in questions:
                            running[pool.submit(one, (n,) + q)] = n
                if not running:
                    break
                done, _ = concurrent.futures.wait(running, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    n = running.pop(future)
                    _, qkind, k, got, what, log, why, p, finish = future.result()
                    job = jobs_of[n]
                    job["results"][(qkind, k)] = (got, what, log, why, p)
                    if got is None:
                        run["declined"] = run["declined"] + 1 if finish in llm.Provider.REFUSED else 0
                        run["failed"] = run["failed"] + 1 if why.startswith("the provider:") else 0
                    else:
                        run["declined"] = run["failed"] = 0
                    if len(job["results"]) == job["questions"]:
                        counts["asked"] += 1
                        changed = settle(n, job, number, kind)
                        if changed is not None:
                            counts["proposed"] += 1
                            counts["changed"] += changed
                        left[job["component"]] -= 1
                        if not left[job["component"]]:
                            finished(job["component"])
                    if not run["stopped"] and run["declined"] >= DECLINED_IN_A_ROW:
                        out("dread pilot: the provider declined %d questions in a row; no more are asked" %
                            run["declined"])
                        run["stopped"] = True
                    if not run["stopped"] and run["failed"] >= FAILED_IN_A_ROW:
                        out("dread pilot: the provider failed %d questions in a row; no more are asked (the run "
                            "goes on where it stopped when started again)" % run["failed"])
                        run["stopped"] = True
                if run["stopped"]:              # (the questions not begun yet are not asked)
                    for future in [f for f in running if f.cancel()]:
                        running.pop(future)
        return counts["proposed"], counts["asked"], counts["changed"]

    order = [("refine", 2)] if refine else [("first", 1)] + [("again", k) for k in range(2, passes + 1)]
    for kind, number in order:
        made, asked, changed = one_pass(number, kind)
        run["proposed"] += made
        run["asked"] += asked
        if len(order) > 1:
            out("dread pilot --task name: pass %d (%s first): asked %d, proposed %d, shown names changed %d; "
                "proposals shown %d%s" % (number, "callers" if kind != "first" and number % 2 == 0 else "callees",
                                          asked, made, changed, len(shown_as),
                                          " (confidence %.2f or more)" % gate if gate else ""))
        if run["stopped"] or (kind == "again" and not changed):
            break                           # (no question would change: nothing more to ask)
    spent = {"answers": 0, "input": 0, "output": 0, "cost": None}
    for p in providers:
        sp = p.spent()
        for key in ("answers", "input", "output"):
            spent[key] += sp[key]
        if sp.get("cost") is not None:
            spent["cost"] = (spent["cost"] or 0.0) + sp["cost"]
    return run["proposed"], run["asked"], spent


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


def judged_json(answer):
    """The object of an answer's ```json block, or {}."""
    import json
    m = re.search(r"```(?:json)?\s*\n(.*?)```", answer or "", re.S)
    try:
        data = json.loads(m.group(1)) if m else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def judge_names(provider, pairs, batch=50, out=print, system=None):
    """{key: "same", "close" or "different"} of pairs ([(key, true name, proposed name)]) as a
    provider judges them, batch pairs a question; a pair without a usable verdict is left out. A
    language model's judgement, a second measure beside the word scores (synonyms and abbreviations:
    buf_len against buffer_length shares no word). system: the judge's instructions (JUDGE_SYSTEM:
    a true function name against a proposed one)."""
    out_map = {}
    for start in range(0, len(pairs), batch):
        part = pairs[start:start + batch]
        user = "\n".join("%d. %s | %s" % (k + 1, t, p) for k, (_, t, p) in enumerate(part))
        try:
            answer = provider.ask(system or JUDGE_SYSTEM, [{"role": "user", "content": user}])
        except Exception as e:
            out("dread pilot --judge: no verdicts for pairs %d-%d: %s" % (start + 1, start + len(part), e))
            continue
        data = judged_json(answer)
        for k, (key, _, _) in enumerate(part):
            v = str(data.get(str(k + 1), "")).strip().lower()
            if v in VERDICTS:
                out_map[key] = v
    return out_map


PAIR_SYSTEM = """You compare names proposed for functions of a program with their true names. On each
numbered line the first name is the true one, given by people who read the code knowing the whole
program; A and B were proposed from one function's code. Say for each line which proposal names the
function of the true name more closely: "A", "B", or "tie" when they are equally close (both right, in
other words or abbreviations too, or both as far off). Answer with one ```json block that maps every
line's number to its word:

```json
{"1": "A", "2": "tie", "3": "B"}
```"""
PAIR_SIDE = {"a": 1, "b": -1, "tie": 0}


def judge_pairs(provider, items, batch=40, out=print):
    """{key: "a", "b" or "tie"} of items ([(key, true name, name a, name b)]) as a provider judges
    which of two proposals is closer to the true name, batch lines a question. Every line is asked
    twice, a and b shown first in turn (a model leans towards a position): a side wins when the two
    verdicts add up to it (both, or one and a tie), else it is a tie; a line without both verdicts is
    left out."""
    def ask(lines):
        got = {}
        for start in range(0, len(lines), batch):
            part = lines[start:start + batch]
            user = "\n".join("%d. %s | A: %s | B: %s" % (k + 1, t, x, y) for k, (_, t, x, y) in enumerate(part))
            try:
                answer = provider.ask(PAIR_SYSTEM, [{"role": "user", "content": user}])
            except Exception as e:
                out("dread pilot --against: no verdicts for lines %d-%d: %s" % (start + 1, start + len(part), e))
                continue
            data = judged_json(answer)
            for k, (key, _, _, _) in enumerate(part):
                v = str(data.get(str(k + 1), "")).strip().lower()
                if v in PAIR_SIDE:
                    got[key] = v
        return got
    first = ask(items)
    second = ask([(key, t, b, a) for key, t, a, b in items])
    out_map = {}
    for key, _, _, _ in items:
        if key in first and key in second:
            s = PAIR_SIDE[first[key]] - PAIR_SIDE[second[key]]
            out_map[key] = "a" if s > 0 else "b" if s < 0 else "tie"
    return out_map


def sign_test(wins, losses):
    """The two-sided sign test's p-value of wins against losses (ties left out): how often a split as
    uneven comes up when either side is as likely to win."""
    import math
    n = wins + losses
    if not n:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(wins, losses) + 1)) / 2 ** n)


def breakdown(rows, verdicts):
    """{"n", "same", "close", "different", "judged", "shared_word", "overlap"} of scored rows (dread.naming
    score_functions') with their verdicts."""
    n = len(rows)
    out = {"n": n, "judged": sum(1 for r in rows if r["address"] in verdicts),
           "shared_word": sum(r["overlap"] > 0 for r in rows), "overlap": sum(r["overlap"] for r in rows) / n if n else 0}
    for v in VERDICTS:
        out[v] = sum(1 for r in rows if verdicts.get(r["address"]) == v)
    return out


def score_names(target, image, truth_path, out=print, judge=None, upto_pass=None):
    """The image's proposals (names.tsv; upto_pass: as they were after that pass) against true names
    (dread.naming.function_truth): prints a line a function into BUILD/pilot/IMAGE/names-score.tsv
    (names-score-passN.tsv) and the totals; judge, a provider: its verdicts as well (judge_names).
    Proposals with a confidence (--samples) get a line per confidence (the calibration: how often
    names so sure are right); proposals with an ablation (--ablate) a line per class: the name
    depends on the names shown or not, and, where the question showed none, the votes' own noise."""
    from dread import naming
    rows = last_rows(read_name_rows(names_path(target, image), upto_pass))
    true = naming.function_truth(truth_path)
    scored, t = naming.score_functions({a: r["proposed"] for a, r in rows.items()}, true)
    verdicts = judge_names(judge, [(r["address"], r["true"], r["proposed"]) for r in scored], out=out) \
        if judge is not None and scored else {}
    suffix = "-pass%d" % upto_pass if upto_pass is not None else ""
    path = os.path.join(target.build, "pilot", image.name, "names-score%s.tsv" % suffix)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("address\ttrue\tproposed\texact\tsame_words\toverlap\tsame_prefix\tjudged\tconfidence\tdepends\t"
                "names_shown\n")
        for r in scored:
            row = rows[r["address"]]
            f.write("0x%x\t%s\t%s\t%d\t%d\t%.2f\t%s\t%s\t%s\t%s\t%s\n" % (
                r["address"], r["true"], r["proposed"], r["exact"], r["same_words"], r["overlap"],
                "" if r["prefix"] is None else int(r["prefix"]), verdicts.get(r["address"], ""),
                row["confidence"], row["depends"], row["names_shown"]))
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

    def line(label, part):
        b = breakdown(part, verdicts)
        judged = ""
        if b["judged"]:
            judged = ", judged the same %d (%.0f %%), close %d (%.0f %%), different %d (%.0f %%)" % tuple(
                x for v in VERDICTS for x in (b[v], 100.0 * b[v] / b["judged"]))
        out("dread pilot --score: %s: %d%s; a word shared %.0f %%, mean overlap %.2f" % (
            label, b["n"], judged, 100.0 * b["shared_word"] / b["n"], b["overlap"]))
        return b
    sure = sorted({confidence(rows[r["address"]]) for r in scored} - {None})
    t["calibration"] = {}
    for c in sure:
        t["calibration"][c] = line("confidence %.2f" % c, [r for r in scored if confidence(rows[r["address"]]) == c])
    t["subsystems"] = score_subsystems(target, image, truth_path, rows, out=out, judge=judge)
    t["ablation"] = {}
    for label, depends, alone in (("names shown, the name depends on them", "yes", False),
                                  ("names shown, the name stands alone", "no", False),
                                  ("no name shown, the votes differ (noise)", "yes", True),
                                  ("no name shown, the votes agree", "no", True)):
        part = [r for r in scored if rows[r["address"]]["depends"] == depends
                and (rows[r["address"]]["names_shown"] in ("", "0")) == alone]
        if part:
            t["ablation"][(depends, alone)] = line(label, part)
    return t


def compare_names(target, image, truth_path, against, judge, out=print, upto_pass=None):
    """This image's proposals (names.tsv; upto_pass: as they were after that pass) against another
    run's (against, a names.tsv of the same image), judged pairwise: every function with a true name
    whose two proposals differ (case aside) goes to judge_pairs, which says which one is closer to the
    true name; wins, losses and ties with the sign test's p (sign_test), a line a function into
    BUILD/pilot/IMAGE/names-pairwise.tsv (names-pairwise-passN.tsv). Asks no absolute verdicts:
    names-score.tsv stays as the last --score wrote it."""
    from dread import naming
    rows = last_rows(read_name_rows(names_path(target, image), upto_pass))
    other = last_rows(read_name_rows(against))
    true = naming.function_truth(truth_path)
    both = sorted(set(rows) & set(other) & set(true))
    items = [(a, true[a], rows[a]["proposed"], other[a]["proposed"]) for a in both
             if rows[a]["proposed"].lower() != other[a]["proposed"].lower()]
    pairs = judge_pairs(judge, items, out=out)
    wins = sum(1 for v in pairs.values() if v == "a")
    losses = sum(1 for v in pairs.values() if v == "b")
    ties = sum(1 for v in pairs.values() if v == "tie")
    t = {"both": len(both), "differ": len(items), "judged": len(pairs), "wins": wins, "losses": losses, "ties": ties,
         "p": sign_test(wins, losses)}
    suffix = "-pass%d" % upto_pass if upto_pass is not None else ""
    path = os.path.join(target.build, "pilot", image.name, "names-pairwise%s.tsv" % suffix)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("address\ttrue\tthis\tother\tcloser\n")
        for a, tn, x, y in items:
            f.write("0x%x\t%s\t%s\t%s\t%s\n" % (a, tn, x, y, {"a": "this", "b": "other", "tie": "tie"}.get(
                pairs.get(a), "")))
    out("dread pilot --score --against %s: %d of %d proposals with a true name differ; judged by %s in both "
        "orders, %d: this run's closer %d, the other's closer %d, ties %d (sign test p = %.3g; %s)" % (
            against, len(items), len(both), judge.label(), len(pairs), wins, losses, ties, t["p"], path))
    return t


def subsystems_task(target, image, program, flow, args, provider):
    """dread pilot --task subsystems: the image's groups (dread.groups find) named where subsystems.tsv
    has no name for them yet (name_subsystems: --samples questions a group, --jobs at a time), each
    question showing the functions by their proposals (names.tsv) where they have one; --dry-run prints
    the questions instead."""
    import threading
    from dread import groups as grouping
    namer = Namer(target, image, program, flow)
    touched = grouping.evidence(target, image, program, flow)
    found = grouping.find(target, image, program, flow, touched=touched)
    namer.prepare_code([fn.name for fn in program.functions if may_show(target, fn.name)])
    shown_as = {program.by_entry[a].name: p for a, (p, _) in read_proposals(target, image).items()
                if a in program.by_entry}
    if args.dry_run:
        known = read_subsystems(subsystems_path(target, image))
        asked = 0
        for k, group in enumerate(found):
            if members_key(program, group) in known or len(group) < SUBSYSTEM_SMALLEST:
                continue
            asked += 1
            print("=" * 72 + "\ngroup %d\n" % k + "-" * 72 + "\n" + subsystem_question(namer, group, touched, shown_as))
        print("dread pilot --task subsystems --dry-run: %d functions in %d groups, %d would be asked about; nothing "
              "asked" % (len(program.functions), len(found), asked))
        return 0
    local, providers, lock = threading.local(), [], threading.Lock()

    def one_provider():                     # (a provider a thread: as name_functions does)
        if getattr(local, "provider", None) is None:
            local.provider = provider if not providers else llm.load(args.provider)
            with lock:
                providers.append(local.provider)
        return local.provider
    named = name_subsystems(target, image, program, one_provider, found, namer, touched, args.samples, args.jobs,
                            min(args.tries, 3), args.temperature, shown_as)
    spent = {"answers": 0, "input": 0, "output": 0, "cost": None}
    for p in providers:
        sp = p.spent()
        for key in ("answers", "input", "output"):
            spent[key] += sp[key]
        if sp.get("cost") is not None:
            spent["cost"] = (spent["cost"] or 0.0) + sp["cost"]
    print("dread pilot --task subsystems: %d functions in %d groups, %d of them in named groups (%s); %d answers, "
          "%d tokens in, %d out%s" % (len(program.functions), len(found), len(named), subsystems_path(target, image),
                                      spent["answers"], spent["input"], spent["output"],
                                      " ($%.2f)" % spent["cost"] if spent["cost"] is not None else ""))
    return 0


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
    parser.add_argument("--refine", action="store_true",
                        help="--task name: a second pass, callers first, over the functions with a proposal")
    parser.add_argument("--behaviour", action="store_true",
                        help="--task name: each question also shows what the function did in runs of its cases "
                             "(dread.behaviour: cached cases, or a short search for a function without)")
    parser.add_argument("--judge", metavar="PROVIDER",
                        help="with --score: a provider also judges each pair the same, close or different")
    parser.add_argument("--samples", type=int, default=NAME_SAMPLES, metavar="K",
                        help="--task name: ask each function K times and keep the name most answers agree with, "
                             "with the agreement as its confidence (default %d)" % NAME_SAMPLES)
    parser.add_argument("--temperature", type=float,
                        help="--task name: the sampling temperature of every question, where the provider takes "
                             "one (Anthropic's Haiku 5.5 and Sonnet 5.5 refuse any but their default 1.0)")
    parser.add_argument("--gate", type=float, metavar="C",
                        help="--task name with --samples: questions show only proposals of confidence C or more")
    parser.add_argument("--passes", type=int, metavar="N",
                        help="--task name: after the first pass, ask again (callers first, then callees first, "
                             "...) the functions whose question changed, until no shown name changes or N passes "
                             "(default %d; 1 with --refine)" % NAME_PASSES)
    parser.add_argument("--subsystems", action=argparse.BooleanOptionalAction, default=NAME_SUBSYSTEMS,
                        help="--task name: group the functions by their code (dread groups), name each group from "
                             "all of its evidence (as --task subsystems), and show each function's question its "
                             "group's name and prefix (default: on; --no-subsystems: off)")
    parser.add_argument("--ablate", action="store_true",
                        help="--task name: ask each function also with no other proposal shown, and flag a name "
                             "that differs then (names.tsv's hidden and depends)")
    parser.add_argument("--upto-pass", type=int, metavar="N",
                        help="with --score: the proposals as they were after pass N")
    parser.add_argument("--against", metavar="NAMES_TSV",
                        help="with --score and --judge: judge pairwise, where they differ, which of this build's "
                             "proposals and another run's names.tsv is closer to the true name (instead of the "
                             "absolute verdicts)")
    parser.add_argument("--task", choices=("write", "input", "name", "readable", "globals", "subsystems"),
                        default="write",
                        help="write C (default), find inputs for unrun instructions, propose names, name the "
                             "variables of readable C (dread readable), name only what the functions share: "
                             "their globals and structures, or name the groups dread groups finds")
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
        if args.against:
            if judge is None:
                print("dread pilot: --against needs --judge (a provider that judges the pairs)", file=sys.stderr)
                return 1
            return 0 if compare_names(target, image, args.score, args.against, judge,
                                      upto_pass=args.upto_pass)["both"] else 1
        return 0 if score_names(target, image, args.score, judge=judge, upto_pass=args.upto_pass)["functions"] else 1
    if args.task == "name" and args.gate and args.samples < 2:
        print("dread pilot: --gate needs --samples 2 or more (one question's proposal has no confidence)",
              file=sys.stderr)
        return 1
    if args.task == "name" and args.refine and (args.passes or 1) > 1:
        print("dread pilot: --refine is a second pass of its own; give it or --passes", file=sys.stderr)
        return 1
    passes = max(1, args.passes or (1 if args.refine else NAME_PASSES))
    if args.task != "write":
        provider = None
        if not args.dry_run:                # (a dry run asks nothing, so it needs no provider)
            try:
                provider = llm.load(args.provider)
            except llm.ProviderError as e:
                print("dread pilot: %s" % e, file=sys.stderr)
                return 1
        if args.task == "subsystems":
            return subsystems_task(target, image, program, flow, args, provider)
        if args.task in ("readable", "globals"):
            from dread import build, lift, readable
            # readable: the functions of the readable table, whose C is built; globals: every function,
            # its readable C checked or not, as the questions for function names show it
            listed = build.read_names(image.table("readable")) if args.task == "readable" else [
                fn.name for fn in program.functions]
            everything = shown(target, listed, out=lambda *_: None)
            names = shown(target, args.names) if args.names else everything
            names = names[:args.limit] if args.limit else names
            if args.dry_run:
                print("\n".join(names))
                print("dread pilot --task %s --dry-run: %s%d functions would be asked about; nothing asked" % (
                    args.task, "the globals and structures of " if args.task == "globals" else "", len(names)))
                return 0
            context = readable.Context(target, image, program, flow)
            lifter = lift.make_lifter(program, flow)
            proposals = {program.by_entry[a].name: p for a, (p, _) in read_proposals(target, image).items()
                         if a in program.by_entry} if args.task == "globals" else None
            made = name_shared(target, image, program, provider, context, lifter, names, everything,
                               shown_as=proposals)
            if args.task == "globals":
                spent = provider.spent()
                print("dread pilot --task globals: %d globals and %d structures named (the table has %d and %d); "
                      "%d answers, %d tokens in, %d out%s" % (
                          made[0], made[1], len(context.globals), len(context.structs), spent["answers"],
                          spent["input"], spent["output"],
                          " ($%.2f)" % spent["cost"] if spent["cost"] is not None else ""))
                return 0
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
            rounds = callees_first(program, names)
            names = [n for r in (reversed(rounds) if args.refine else rounds) for n in r]
            names = names[:args.limit] if args.limit else names
            if args.dry_run:
                refined = set()
                known = read_proposals(target, image, refined)
                entries = {n: program.by_name[n].entry for n in names}
                todo = [n for n in names if (entries[n] in known and entries[n] not in refined if args.refine
                                             else entries[n] not in known)]
                print("\n".join(todo))
                print("dread pilot --task name --dry-run: %d functions would be asked about, %s first (%d %s "
                      "already); nothing asked" % (len(todo), "callers" if args.refine else "callees",
                                                   len(names) - len(todo), "refined or without a first proposal"
                                                   if args.refine else "proposed"))
                return 0
            made, asked, spent = name_functions(target, image, program, lambda: llm.load(args.provider), names,
                                                flow, jobs=args.jobs, tries=min(args.tries, 3), refine=args.refine,
                                                behaviour=args.behaviour, samples=args.samples,
                                                temperature=args.temperature, gate=args.gate, passes=passes,
                                                ablate=args.ablate, subsystems=args.subsystems)
            cost = " ($%.2f)" % spent["cost"] if spent["cost"] is not None else ""
            print("dread pilot --task name: %d of %d asked functions got a proposal%s (%s); %d answers, %d tokens "
                  "in, %d out%s" % (made, asked, ", over the passes" if passes > 1 else "",
                                    names_path(target, image), spent["answers"], spent["input"], spent["output"],
                                    cost))
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
