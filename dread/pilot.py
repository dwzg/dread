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
check a name, so none is applied.

A provider that declines DECLINED_IN_A_ROW questions in a row (stop reason "refusal" or
"content_filter") is asked no more in that run: its classifiers decline the same kind of question
again, and another provider may answer it. On 2026-10-20 Anthropic's Sonnet 5.5 and Opus 5.5 declined
every --task input question on FreeRTOS (a listing with instructions to reach), also with the purpose
stated, while Haiku 5.5 answered the same question.

    dread pilot [--target DIR] [--image NAME] [--provider NAME] [--tries N] [--limit N] [--no-lifted]
                [--no-mutate] [--no-integration] [--dry-run] [--task write|input|name] [NAME...]
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

NAME_SYSTEM = """You name functions of a binary image from their code. A name says what the function
does, in snake_case, with a prefix for its subsystem when one is clear (uart_, gpio_, timer_, cfg_).
Answer with exactly two lines:

name: <the name>
what: <one sentence: what the function does>"""


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
    from unicorn import UC_HOOK_BLOCK, UC_HOOK_CODE
    sites = {a: program.insn_size(a) for a in fn.insns if program.arch.flow(program.insn(a))[0] == "icall"}
    if not sites:
        return None
    m = gen.machine()
    m.plan = []
    gen.apply(m, case)
    state = {"after": None, "wild": None}

    def at_site(uc, address, size, user):
        state["after"] = address + sites[address]

    def on_block(uc, address, size, user):
        after, state["after"] = state["after"], None
        if after is not None and address != after and address not in program.by_entry and state["wild"] is None:
            state["wild"] = address

    for address in sites:
        m.hook_add(UC_HOOK_CODE, at_site, begin=address, end=address)
    m.hook_add(UC_HOOK_BLOCK, on_block)
    m.may_hang = bool(case.get("hang"))
    try:
        m.call(fn.entry, *case["args"], max_instructions=case.get("limit", 200_000))
    except Exception:
        pass
    return state["wild"]


def find_inputs(target, image, program, provider, name, missed, tries=3, flow=None, out=print):
    """Ask for inputs that run the missed instructions; each one that runs some becomes a written
    case of the function (extra_cases, "by" the pilot). Returns the addresses still unrun."""
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
    user = ("The function %s:\n\n```\n%s\n```\n\nThe instructions marked >> never ran under any input yet: "
            "%s. Give one input that runs as many of them as you can." % (
                name, "\n".join(marked), " ".join("0x%x" % a for a in sorted(still))))
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
            ran = gen.coverage(fn.entry, fn.insns, case, case["limit"], check_image=True)
            new = ran & still
            wild = wild_call(gen, program, fn, case) if new else None
            if not ran:
                feedback = ("The input does not finish (it faults, runs on beyond its limit, or loads code as "
                            "data or writes into the image). Give another one.")
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


def propose_name(target, image, program, provider, name, flow=None, out=print):
    """Ask for a name and a sentence; a proposal goes to BUILD/pilot/IMAGE/names.tsv, never into the
    tables (no tool can check a name). Returns (name, what) or None."""
    from dread import flat
    fn = program.by_name[name]
    user = "The function %s:\n\n```\n%s\n```" % (name, "\n".join(
        flat.listing(program, fn, flow, callers=flat.callers_of(program))))
    try:
        answer = provider.ask(NAME_SYSTEM, [{"role": "user", "content": user}])
    except Exception as e:
        out("%-24s no proposal: %s" % (name, e))
        return None
    if (getattr(provider, "last", {}) or {}).get("finish") in llm.Provider.REFUSED:
        out("%-24s no proposal: the provider declined" % name)
        return None
    proposed = re.search(r"^\s*name\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?\s*$", answer, re.M | re.I)
    what = re.search(r"^\s*what\s*:\s*(.+?)\s*$", answer, re.M | re.I)
    if not proposed or not what or len(proposed.group(1)) > 48 or proposed.group(1) in program.by_name:
        out("%-24s no usable proposal" % name)
        return None
    folder = os.path.join(target.build, "pilot", image.name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "names.tsv")
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write("address\tnow\tproposed\twhat\tprovider\n")
        f.write("0x%x\t%s\t%s\t%s\t%s\n" % (fn.entry, name, proposed.group(1), what.group(1).replace("\t", " "),
                                            provider.label()))
    out("%-24s proposed %s: %s" % (name, proposed.group(1), what.group(1)))
    return proposed.group(1), what.group(1)


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
    parser.add_argument("--dry-run", action="store_true", help="print the question for each function, ask nothing")
    parser.add_argument("--usage", nargs="?", const="", metavar="YYYY-MM",
                        help="print the tokens (and costs, where llm.toml gives prices) of a month's answers; ask nothing")
    parser.add_argument("--task", choices=("write", "input", "name"), default="write",
                        help="write C (default), find inputs for unrun instructions, or propose names")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
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
    if args.task != "write":
        try:
            provider = llm.load(args.provider)
        except llm.ProviderError as e:
            print("dread pilot: %s" % e, file=sys.stderr)
            return 1
        if args.task == "name":
            from dread.samecode import UNNAMED
            names = shown(target, args.names) if args.names else sorted(
                (fn.name for fn in program.functions if UNNAMED.match(fn.name) and may_show(target, fn.name)),
                key=lambda n: len(program.by_name[n].insns))[:args.limit]
            asking = Asking(provider)
            for name in names:
                if not asking.go_on():
                    break
                propose_name(target, image, program, provider, name, flow)
                asking.asked()
            return 0
        missed = missed_instructions(target, image, args.names or [fn.name for fn in program.functions])
        for name, addresses in lift_missed(target, image, program).items():      # what the lifter left
            if not args.names or name in args.names:
                missed.setdefault(name, addresses)
        missed = {n: missed[n] for n in shown(target, sorted(missed))}
        started, before = time.time(), llm.usage_summary().get(provider.name, {})
        asked = sorted(missed.items(), key=lambda kv: (len(kv[1]), kv[0]))[:args.limit]     # fewest first
        line = input_task(target, image, program, provider, asked, args.tries, flow)
        after = llm.usage_summary().get(provider.name, {})
        spent = {k: (after.get(k) or 0) - (before.get(k) or 0) for k in ("answers", "input", "output", "cost")}
        print("dread pilot --task input: %s; %d answers, %d tokens in, %d out%s, %.0f s" % (
            line, spent["answers"], spent["input"], spent["output"],
            ", $%.2f" % spent["cost"] if after.get("cost") is not None else "", time.time() - started))
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
