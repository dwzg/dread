"""A no-confidence vote on an image's existing function names (dread pilot --task review).

Reverse engineering is like a puzzle (the user, 2026-10-22): a piece can look right in one context
and wrong in another, and a part of the picture can be consistent inside and still wrong. The naming
runs (dread pilot --task name) put pieces down; the review takes them up again where code finds a
reason, and puts a new one down only when it wins against the old one by a margin.

1. Triggers, computed by code for every name (function_reasons, group_reasons): what the image
   holds against it, each a reason with a weight (WEIGHTS) and a sentence, the weights summed into
   its score. For a function: the vote that chose it agreed little (names.tsv's confidence) or its
   answers fell into several readings (SPLIT); another function has the same name, case and
   separators aside (a name should tell a function from its neighbours); the name says little beyond
   its group's prefix (VAGUE words); it does not begin with its group's prefix though most of its
   group's names do; functions of SHARED or more other parts call it and its name puts it into one of
   them (a helper named after one use); it points to a source file's path (an assertion's __FILE__)
   whose file name shares no word with its name nor with its group's. For a group, tested at its edges
   (a group's names agree inside it, so only what lies at its edges can contradict them): the vote
   over its prefix; the source files its functions' texts name, when half of those that name one name
   a file that shares no word with the group's name; the devices the target describes ([models], and
   the data symbols at their registers): most functions that use one in the group and its name not
   saying so, or its name naming one that none of its functions touches; fewer than half of its
   functions taking its prefix. Names people gave (the tables' names, not FUN_) are tested the same
   way, without a vote.
2. Challenges: each group whose score reaches GROUP_THRESHOLD, then the functions whose score
   reaches THRESHOLD, highest first (--limit a round at most). A challenge asks the naming provider
   for a candidate, samples times: the naming question as dread pilot --task name asks it (the
   function's readable C, its callers, its group's line, the names so far) with a note: its name, its
   sentence and the reasons (CHALLENGE_NOTE); dread.naming.vote picks the candidate. A candidate with
   the old name's words (alike()) confirms it. Else a pairwise question decides (the decider:
   --decider, default the naming provider), asked in both orders (A and B swapped: a model leans
   towards a position), neither name marked as the old one: which of the two says better what the
   function does, "same", or "general": both describe one use of a function that serves more (a
   helper several parts share), with a more general name. The change needs a margin (MARGIN,
   hysteresis): the votes for the candidate (or for "general") must exceed those for the old name and
   "same" together by MARGIN; with one question in each order and a margin of 2, both orders have to
   prefer it. A group's challenge asks for a name, prefix and sentence from all of its evidence (the
   --task subsystems question with GROUP_NOTE) and decides the same way; when its name changes, every
   function of the group is challenged in the same round (the reason "renamed"), its questions
   showing the new line.
3. Bookkeeping: every challenge is a line of BUILD/pilot/IMAGE/review.tsv (REVIEW_COLUMNS,
   docs/formats.md): its reasons and score, the old and the candidate name with their sentences, the
   candidate's agreement, the decision's votes, the outcome (OUTCOMES) and the name it leaves. A change
   is a new row of names.tsv (its pass the last pass so far plus the round, its shown column marked
   REVIEWED) or of subsystems.tsv (a group's), so every reader takes it; --upto-pass with the last
   naming pass gives the names before the review. A name people gave is never changed: a challenge it
   loses is logged as "flagged", with the name preferred, for a person to look at. The conversations
   go to BUILD/pilot/IMAGE/review/.
4. Rounds: after a round the triggers are computed again with the names it changed. A function or a
   group changes at most once in a review (names that depend on each other could oscillate), and a
   name challenged before, in this review or an earlier one (review.tsv's reason_keys), is challenged
   again only for a reason its last challenge lacked or under another name. At most ROUNDS rounds
   (--rounds); the review ends early when a round challenges nothing, or when none of a round's
   challenges got a candidate (a provider that fails).

What it does not establish: a trigger is a reason to look, not a fault, and a name the code gives no
reason against is not looked at (a misreading of what the code does, such as receive for peek, or two
functions' names swapped, leaves no trace in the tables; nor does a misread group whose functions
point to no source file and use no described device). The decider is a language model and its
verdicts are not checked by a tool. Measured on FreeRTOS only (docs/decisions.md, 2026-10-23).
"""
import collections
import os
import re

from dread import llm

# Triggers: the weight of each reason (summed into a name's score); constants of the method,
# measured on FreeRTOS (docs/decisions.md, 2026-10-23), overridable through review(weights=...).
WEIGHTS = {
    "vote": 2.0,        # times (1 - the vote's confidence): 0.8 at 0.6, 1.2 at 0.4
    "split": 0.5,       # the vote's answers fell into SPLIT or more readings
    "same": 1.0,        # another function has the same name
    "vague": 1.0,       # the name says little beyond its prefix
    "prefix": 0.6,      # it does not begin with its group's prefix, though most of the group's do
    "shared": 0.6,      # functions of SHARED other parts call it, its name puts it into one
    "file": 1.0,        # a source file its texts name shares no word with its name nor its group's
    "device": 1.0,      # (groups) most users of a described device are in it, its name does not say so
    "unused": 0.6,      # (groups) its name names a described device whose registers it never touches
    "members": 0.6,     # (groups) fewer than half of its functions take its prefix
    "renamed": 1.0,     # (functions) its group was renamed in this review
}
THRESHOLD = 1.0         # a function whose score reaches this is challenged
GROUP_THRESHOLD = 1.0   # and a group
MARGIN = 2              # the votes a candidate needs beyond those for the old name and "same" together
ROUNDS = 2              # rounds of a review at most
SPLIT = 3               # readings (groups of alike answers) of a vote that count as a split vote
SHARED = 2              # other parts whose functions call a function that make it a shared helper
REASONS_SHOWN = 6       # reasons a challenge's note shows at most
REVIEWED = ", review"   # names.tsv's shown column of a proposal a review made

# Words that say little about what a function does: a name whose words beyond its prefix are all
# among them says little (compared in dread.naming's canonical form; main, a program's entry by
# convention, is not among them). A method's constant.
VAGUE = ("task", "handler", "handle", "helper", "func", "function", "routine", "process", "do", "run", "util",
         "utility", "misc", "common", "generic", "wrapper", "stub", "thunk", "internal", "op", "operation", "work",
         "worker", "loop", "step", "update", "data", "value", "action", "callback", "hook", "entry", "core", "impl",
         "body", "logic", "proc", "fn", "sub", "code", "thing", "stuff", "unknown")

# A text that is a source file's path (an assertion's __FILE__): its file name without the extension.
SOURCE_PATH = re.compile(r"(?:^|[\\/])([A-Za-z0-9_\-]+)\.(?:c|h|cc|cpp|cxx|hpp|s|S|asm)$")

REVIEW_COLUMNS = ("review", "round", "kind", "address", "now", "group", "source", "score", "reasons", "old",
                  "old_what", "candidate", "candidate_what", "agreement", "votes", "outcome", "name", "what",
                  "provider", "decider", "reason_keys")
OUTCOMES = ("confirmed", "kept", "changed", "general", "flagged", "no candidate")

CHALLENGE_NOTE = """{who} named this function {name}{what}
Tools that compare names with the image found reasons to look at that name again:
{reasons}
Read the code again and answer with the name that says best what the function does: {name} again if it
does, another one if it does not."""

GROUP_NOTE = """A first reading named this part {name}, with the prefix {prefix}: {what}
Tools that compare it with what lies at its edges found reasons to look at it again:
{reasons}
Answer with the name, the prefix and the sentence that fit the part best: the same again if they do."""

DECIDE_SYSTEM = """You compare two names proposed for one function of a binary image, from its code. The code
below is C made from the machine code by tools (variables named after the registers and stack slots
that held them, memory at fixed addresses written g_ADDRESS, io_ADDRESS for device registers,
rom_ADDRESS in the image), or the machine code itself where no C could be made. A function without a
name is written FUN_ and its address; some names of other functions are proposals made from their
code, likely but not certain. A comment after a constant shows what it points to. After the code: two
names for the function, A and B, each with a sentence on what it does. Say which name says better what
the function does: its purpose, specific enough to tell it from the functions around it, and nothing
it does not do. Answer with one line:

better: <A, B, same or general>

"same": both say it equally well. "general": both describe only one use, or one caller's view, of a
function that serves more (a helper several parts share); then add two lines:

name: <a more general name, in snake_case>
what: <one sentence: what the function does>"""

GROUP_DECIDE_SYSTEM = """You compare two names proposed for one part of a binary image. Tools grouped its
functions from their code alone: the memory, device registers and texts they share, the functions
whose addresses one of them takes, and their places in the image. Below: the part's evidence (its
functions' code as C made from the machine code, the texts they point to, the devices they use, the
functions around it), then two names for the part, A and B, each with the prefix its functions' names
would share and a sentence. Say which fits the part better: what the part is, and nothing it is not.
Answer with one line:

better: <A, B, same or general>

"same": both fit equally well. "general": both name only one side of a part that is more; then add
three lines:

name: <a more general name for the part>
prefix: <its prefix, snake_case, ending in an underscore>
what: <one sentence: what the part does>"""


class Reason:
    """One trigger: kind (a key of WEIGHTS), weight, and a sentence for the note and the log."""

    def __init__(self, kind, weight, text):
        self.kind, self.weight, self.text = kind, weight, text

    def __repr__(self):
        return "Reason(%r, %.2f, %r)" % (self.kind, self.weight, self.text)


def score_of(reasons):
    return round(sum(r.weight for r in reasons), 6)


def reason_keys(reasons):
    """The reasons' digests (review.tsv's reason_keys): a later challenge needs one that is not among
    them, or another name."""
    import hashlib
    return {hashlib.sha1(r.text.encode()).hexdigest()[:10] for r in reasons}


def reasons_text(reasons):
    """The reasons of a challenge as the log keeps them: "kind weight: sentence; ..."."""
    return "; ".join("%s %.2f: %s" % (r.kind, r.weight, r.text) for r in reasons)


_VAGUE = None


def vague_words():
    """VAGUE in dread.naming's canonical form (made once)."""
    global _VAGUE
    if _VAGUE is None:
        from dread.naming import canonical_word
        _VAGUE = frozenset(canonical_word(w) for w in VAGUE)
    return _VAGUE


class Picture:
    """What the triggers read, made once for a review and updated as names change: the names (the
    last proposal of each function in names.tsv, else its name in the tables: a person's when it is
    not of the unnamed form), the groups (groups.tsv and subsystems.tsv: the grouping the naming run
    named), what each function touches (dread.groups evidence: from the listing), its callers and
    callees, the devices the target describes ([models] with a base and a size, and the data symbols
    at their registers)."""

    def __init__(self, target, image, program, flow=None, touched=None):
        from dread import groups as grouping, pilot
        from dread.flat import callers_of
        from dread.samecode import UNNAMED
        self.target, self.image, self.program, self.flow = target, image, program, flow
        rows = pilot.read_name_rows(pilot.names_path(target, image))
        self.rows = pilot.last_rows(rows)
        self.passes = max((int(r["pass"]) for r in rows if r["pass"].isdigit()), default=0)
        self.names, self.whats, self.sources = {}, {}, {}
        annotations = program.annotations() if hasattr(program, "annotations") else {}
        for fn in program.functions:
            row = self.rows.get(fn.entry)
            if not UNNAMED.match(fn.name):
                self.names[fn.name], self.sources[fn.name] = fn.name, "person"
                lines = annotations.get(fn.entry) or []
                self.whats[fn.name] = " ".join(lines[0].split()) if lines else ""
            elif row is not None:
                self.names[fn.name], self.sources[fn.name] = row["proposed"], "proposal"
                self.whats[fn.name] = row["what"]
        self.subsystems = {}                # {members key: subsystems.tsv row}
        self.members = collections.OrderedDict()    # {members key: [function names in address order]}
        self.group_of, self.number = {}, {}         # {function name: members key}, {members key: number}
        rows = pilot.read_subsystems(pilot.subsystems_path(target, image))
        for r in pilot.read_table(pilot.groups_path(target, image), pilot.GROUP_COLUMNS):
            try:
                fn = program.by_entry.get(int(r["address"], 16))
            except ValueError:
                continue
            if fn is None or not r["members"]:
                continue
            self.members.setdefault(r["members"], []).append(fn.name)
            self.group_of[fn.name] = r["members"]
            self.number.setdefault(r["members"], r["group"])
        for key, names in self.members.items():
            names.sort(key=lambda n: program.by_name[n].entry)
            if key in rows:
                self.subsystems[key] = rows[key]
        if touched is None:
            touched = grouping.evidence(target, image, program, flow)
        self.touched = touched
        self.callers = callers_of(program)
        self.callees = collections.defaultdict(set)
        for callee, sites in self.callers.items():
            for caller, _ in sites:
                self.callees[caller].add(callee)
        self.devices = self.described_devices()
        self.users = collections.defaultdict(set)   # {device block: the functions that use its registers}
        for n, things in touched.items():
            for kind, key in things:
                if kind == "device":
                    self.users[key].add(n)

    # ---- names

    def name(self, n):
        """The name a function has now: its proposal, a person's name, or its unnamed name."""
        return self.names.get(n, n)

    def what(self, n):
        return self.whats.get(n, "")

    def shown_as(self):
        """{function name: the proposal shown for it}: what the naming questions show (Namer)."""
        return {n: p for n, p in self.names.items() if self.sources.get(n) == "proposal"}

    def subsystem_lines(self):
        """{function name: (its group's size, its subsystems.tsv row)} for Namer.subsystem."""
        out = {}
        for key, names in self.members.items():
            row = self.subsystems.get(key)
            if row is not None:
                for n in names:
                    out[n] = (len(names), row)
        return out

    def set_name(self, n, name, what):
        self.names[n], self.whats[n] = name, what
        self._spelled = None

    def twins(self, n):
        """The other functions whose names are n's but for case and separators (an index made once for
        the names as they are: a scan per function would be quadratic in thousands of functions)."""
        if getattr(self, "_spelled", None) is None:
            self._spelled = collections.defaultdict(list)
            for m in self.names:
                if self.sources.get(m):
                    self._spelled[spelled(self.name(m))].append(m)
        return [m for m in self._spelled.get(spelled(self.name(n)), ()) if m != n]

    # ---- what a function touches

    def texts(self, n):
        """The texts a function points to (dread.constants string_at, whole), in address order."""
        from dread.constants import string_at
        out = []
        for kind, key in self.touched.get(n, ()):
            if kind == "text":
                text = string_at(self.program, key, limit=4096)
                if text:
                    out.append(text)
        return out

    def files(self, n):
        """The source files a function's texts name ({file name without extension: the text})."""
        out = {}
        for text in self.texts(n):
            m = SOURCE_PATH.search(text)
            if m and (len(m.group(0)) < len(text) or "/" in text or "\\" in text or text == m.group(0)):
                out.setdefault(m.group(1), text)
        return out

    def described_devices(self):
        """[(label, words, name words, blocks)] of the devices the target describes: its [models] with a
        base, labelled by their name and kind; blocks, the device blocks (dread.groups DEVICE_BLOCK) its
        registers lie in (from its base up to its size, the base's block alone without a size); words,
        those of its name and kind and of the data symbols (dread.program symbols, kind "d") at its
        registers; name words, those of its name alone (three letters or more, digits dropped)."""
        from dread import groups as grouping
        from dread.naming import style_words
        out = []
        models = getattr(self.target, "models", {}) or {}
        try:
            symbols = [(a, name) for kind, a, name in self.program.symbols() if kind == "d"]
        except Exception:                   # (an image without a symbols table)
            symbols = []
        for name, spec in sorted(models.items()):
            if not isinstance(spec, dict) or not isinstance(spec.get("base"), int):
                continue
            base, size = spec["base"], spec.get("size")
            kind = str(spec.get("kind", ""))
            end = base + size if isinstance(size, int) and size > 0 else base + 1
            blocks = frozenset(range(base // grouping.DEVICE_BLOCK, (end - 1) // grouping.DEVICE_BLOCK + 1))
            words = set(style_words(name)) | set(style_words(kind))
            words |= {w for a, sym in symbols if base <= a < end for w in style_words(sym)}
            label = "%s (%s)" % (name, kind) if kind and kind != name else name
            out.append((label, frozenset(w for w in words if not w.isdigit()),
                        frozenset(w for w in style_words(name) if len(w) >= 3 and not w.isdigit()), blocks))
        return out

    def blocks(self, n):
        return sorted(key for kind, key in self.touched.get(n, ()) if kind == "device")


def spelled(name):
    """A name with case and separators aside (strcmp, str_cmp and StrCmp alike)."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def shares_word(a, b):
    """Whether two collections of style words share a word (dread.naming same_word: one begins the other)."""
    from dread.naming import same_word
    return any(same_word(x, y) for x in a for y in b)


def group_words(row):
    """The words of a group's name and prefix (style words): what its functions' names take from it (its
    sentence says more, and its many words would match nearly any file)."""
    from dread.naming import style_words
    if not row:
        return set()
    return set(style_words(row.get("name", ""))) | set(style_words(row.get("prefix", "")))


def split_readings(samples):
    """The number of readings among a vote's answers (names.tsv's samples column): groups of answers
    alike (dread.naming.similarity AGREE or more to the group's first)."""
    from dread import naming
    readings = []
    for s in (samples or "").split():
        if s == "-":
            continue
        if not any(naming.similarity(r, s) >= naming.AGREE for r in readings):
            readings.append(s)
    return len(readings)


def function_reasons(picture, n, weights=None, renamed=None):
    """[Reason] against the name function n has now (Picture), empty for a function without a name of
    either kind. renamed: {members key: (old row, new row)} of the groups this review renamed."""
    from dread import naming, pilot
    w = dict(WEIGHTS, **(weights or {}))
    source = picture.sources.get(n)
    if source is None:
        return []
    program = picture.program
    name = picture.name(n)
    out = []
    row = picture.rows.get(program.by_name[n].entry) if source == "proposal" else None
    if row is not None and row.get("proposed") == name:
        c = pilot.confidence(row)
        if c is not None and c < 1:
            out.append(Reason("vote", w["vote"] * (1 - c), "the vote that chose it agreed %.0f %% (its answers: %s)"
                              % (100 * c, row.get("samples", ""))))
        readings = split_readings(row.get("samples"))
        if readings >= SPLIT:
            out.append(Reason("split", w["split"], "its answers fell into %d readings" % readings))
    twins = picture.twins(n)
    if twins:
        shown = ", ".join("%s%s" % (m, " (%s)" % picture.what(m) if picture.what(m) else "") for m in sorted(
            twins, key=lambda m: program.by_name[m].entry)[:3])
        out.append(Reason("same", w["same"], "%s named the same: %s" % (
            "another function is" if len(twins) == 1 else "%d other functions are" % len(twins), shown)))
    key = picture.group_of.get(n)
    group = picture.subsystems.get(key) if key else None
    words = naming.style_words(name)
    prefix = group["prefix"] if group else ""
    head = naming.style_words(prefix) if prefix else []
    rest = [x for x in words if x not in head]
    if not rest or all(x in vague_words() for x in rest):
        out.append(Reason("vague", w["vague"], "its name says little%s" % (
            " beyond its group's prefix %s" % prefix if prefix and words[:len(head)] == head else "")))
    if group and prefix and not pilot.uses_prefix(name, prefix):
        members = picture.members[key]
        using = sum(1 for m in members if picture.sources.get(m) and pilot.uses_prefix(picture.name(m), prefix))
        if 2 * using > len(members):
            out.append(Reason("prefix", w["prefix"], "its name does not begin with %s, the prefix of its group (%s), "
                                                     "which %d of the group's %d functions take" % (
                                                         prefix, group.get("name", ""), using, len(members))))
    caller_groups = {}
    for caller, _ in picture.callers.get(n, []):
        k = picture.group_of.get(caller)
        if k and k != key:
            caller_groups.setdefault(k, caller)
    if len(caller_groups) >= SHARED:
        theirs = [picture.subsystems[k]["prefix"] for k in caller_groups if k in picture.subsystems]
        named_after = [p for p in theirs if pilot.uses_prefix(name, p)]
        if named_after:
            out.append(Reason("shared", w["shared"], "functions of %d other parts call it (%s), but its name puts it "
                                                     "into one of them (%s)" % (
                                                         len(caller_groups), ", ".join(sorted(set(theirs))[:5]),
                                                         named_after[0])))
    own = set(words) | group_words(group)
    for stem, text in sorted(picture.files(n).items()):
        found = set(naming.style_words(stem))
        if found and not shares_word(found, own):
            out.append(Reason("file", w["file"], 'it points to "%s", whose file name shares no word with its name '
                                                 'nor with its group\'s' % text))
            break
    if renamed and key in renamed:
        old, new = renamed[key]
        out.append(Reason("renamed", w["renamed"], "its group was renamed from %s (prefix %s) to %s (prefix %s): %s"
                          % (old.get("name", ""), old.get("prefix", ""), new.get("name", ""), new.get("prefix", ""),
                             new.get("what", ""))))
    return out


def group_reasons(picture, key, weights=None):
    """[Reason] against the name of the group with this members key (Picture.subsystems), tested at
    its edges: the vote over its prefix, the source files its texts name, the devices it uses or
    names, its functions' names that do not take its prefix."""
    from dread import naming, pilot
    w = dict(WEIGHTS, **(weights or {}))
    row = picture.subsystems.get(key)
    if row is None:
        return []
    members = picture.members[key]
    out = []
    c = pilot.confidence(row)
    if c is not None and c < 1:
        out.append(Reason("vote", w["vote"] * (1 - c), "the vote over its prefix agreed %.0f %% (its answers: %s)" % (
            100 * c, row.get("samples", ""))))
    own = group_words(row)
    with_files, against = 0, collections.Counter()
    for n in members:
        files = picture.files(n)
        if not files:
            continue
        with_files += 1
        for stem in files:
            if not shares_word(set(naming.style_words(stem)), own):
                against[stem] += 1
                break
    if with_files and 2 * sum(against.values()) >= with_files:
        out.append(Reason("file", w["file"], "%d of its %d functions that point to a source file's path name a file "
                                             "that shares no word with the part's name (%s)" % (
                                                 sum(against.values()), with_files,
                                                 ", ".join(s for s, _ in against.most_common(3)))))
    inside = set(members)
    held, named = [], []
    title = set(naming.style_words(row.get("name", ""))) | set(naming.style_words(row.get("prefix", "")))
    for label, dwords, nwords, blocks in picture.devices:
        users = set().union(*[picture.users.get(b, set()) for b in blocks])
        here = users & inside
        if dwords and here and 2 * len(here) > len(users) and not shares_word(dwords, own):
            held.append("%s (%d of its %d users)" % (label, len(here), len(users)))
        elif nwords and not here and nwords & title:
            named.append(label)
    if held:
        out.append(Reason("device", w["device"], "most functions that use the registers of %s, a device the target "
                                                 "describes, are in it; its name does not say so" % ", ".join(held)))
    if named:
        out.append(Reason("unused", w["unused"], "its name names %s, a device the target describes, whose registers "
                                                 "none of its functions touches" % ", ".join(named)))
    with_names = [n for n in members if picture.sources.get(n)]
    using = sum(1 for n in with_names if pilot.uses_prefix(picture.name(n), row.get("prefix", "")))
    if with_names and 2 * using < len(with_names):
        out.append(Reason("members", w["members"], "only %d of its %d named functions begin with its prefix %s" % (
            using, len(with_names), row.get("prefix", ""))))
    return out


def ranked(picture, names=None, weights=None, renamed=None):
    """([(score, name, reasons)] of the functions, highest first then by address, and the same of the
    groups by members key): every function of names (default: all) with a name, every named group."""
    program = picture.program
    functions = []
    for n in (names if names is not None else [fn.name for fn in program.functions]):
        reasons = function_reasons(picture, n, weights, renamed)
        if reasons:
            functions.append((score_of(reasons), n, reasons))
    functions.sort(key=lambda x: (-x[0], program.by_name[x[1]].entry))
    groups = []
    for key in picture.subsystems:
        reasons = group_reasons(picture, key, weights)
        if reasons:
            groups.append((score_of(reasons), key, reasons))
    groups.sort(key=lambda x: (-x[0], program.by_name[picture.members[x[1]][0]].entry))
    return functions, groups


# ---- the questions

def note(reasons):
    return "\n".join("- " + r.text for r in sorted(reasons, key=lambda r: -r.weight)[:REASONS_SHOWN])


def candidate_question(namer, picture, n, reasons):
    """The candidate question for function n: the naming question (Namer.question, the names so far
    shown) with CHALLENGE_NOTE: its name, its sentence and the reasons. Returns (text, form, the naming
    question alone)."""
    program = picture.program
    user, form = namer.question(program.by_name[n], picture.shown_as())
    what = picture.what(n)
    who = "People who read the program" if picture.sources.get(n) == "person" else "A first reading"
    text = user + "\n\n" + CHALLENGE_NOTE.format(who=who, name=picture.name(n), what=": " + what if what else ".",
                                                 reasons=note(reasons))
    return text, form, user


def decision_question(user, a, b, group=False):
    """The pairwise question: the evidence (user: the naming question without the note, or the group's
    question) and the two names as A and B (each (name, what) or, for a group, (name, prefix, what)); a
    side without a sentence shows no sentence for either."""
    if group:
        lines = ["A: %s (prefix %s): %s" % a, "B: %s (prefix %s): %s" % b]
    elif a[1] and b[1]:
        lines = ["A: %s: %s" % a, "B: %s: %s" % b]
    else:
        lines = ["A: %s" % a[0], "B: %s" % b[0]]
    return user + "\n\nThe two names:\n" + "\n".join(lines)


DECISION = re.compile(r"^\s*\**better\**\s*:\s*\**`?\s*(A|B|same|general)\b", re.M | re.I)


def parse_decision(answer, group=False):
    """(verdict, general) of a decider's answer: verdict "a", "b", "same" or "general", None without a
    usable line; general, for "general", the more general (name, what) or (name, prefix, what), None
    when its lines are missing (the verdict then counts as none)."""
    from dread import pilot
    m = DECISION.search(answer or "")
    if not m:
        return None, None
    verdict = m.group(1).lower()
    if verdict != "general":
        return verdict, None
    rest = (answer or "")[m.end():]
    general = pilot.parse_subsystem(rest) if group else pilot.parse_name(rest)
    if general is None:
        return None, None
    return "general", general


def ask(provider, system, user, tries=2):
    """(answer or None, log, why) of one question."""
    messages = [{"role": "user", "content": user}]
    log = ["## Question, asked %s\n\n%s" % (provider.label(), user)]
    for _ in range(max(1, tries)):
        provider.last = {}
        try:
            answer = provider.ask(system, messages)
        except Exception as e:
            return None, log, "the provider: %s" % e
        log.append("## Answer\n\n%s" % answer)
        if (getattr(provider, "last", {}) or {}).get("finish") in llm.Provider.REFUSED:
            return None, log, "the provider declined"
        return answer, log, None
    return None, log, "no answer"


def decide(decider, user, old, new, margin=MARGIN, group=False, tries=2):
    """(outcome, votes, general, log) of the pairwise decision between old and new (each (name, what), or
    (name, prefix, what) for a group): asked twice, A the old one first and then the new one; a verdict
    the parser cannot read is asked again (up to tries answers). votes {"old", "new", "same", "general",
    "none"}; outcome "new" or "general" when that side's votes exceed old's and same's together by
    margin, else "old"; general the more general name the general votes agree on most
    (dread.naming.vote on its name or prefix), None unless the outcome is "general"."""
    from dread import naming
    system = GROUP_DECIDE_SYSTEM if group else DECIDE_SYSTEM
    votes = collections.Counter({k: 0 for k in ("old", "new", "same", "general", "none")})
    generals, log = [], []
    for a, b, side in ((old, new, {"a": "old", "b": "new"}), (new, old, {"a": "new", "b": "old"})):
        question = decision_question(user, a, b, group)
        verdict = general = None
        for _ in range(max(1, tries)):
            answer, part, why = ask(decider, system, question, tries=1)
            log += part
            if answer is None:
                break
            verdict, general = parse_decision(answer, group)
            if verdict is not None:
                break
            log.append("## (no line better: A, B, same or general that can be read: asked again)")
        if verdict is None:
            votes["none"] += 1
        elif verdict in side:
            votes[side[verdict]] += 1
        else:
            votes[verdict] += 1
            if verdict == "general":
                generals.append(general)
    keep = votes["old"] + votes["same"]
    best = max(("new", "general"), key=lambda k: (votes[k], k == "new"))
    if votes[best] - keep >= margin and votes[best] > 0:
        if best == "general":
            key = [g[1] if group else g[0] for g in generals]
            voted = naming.vote(key)
            return "general", dict(votes), generals[voted[2] if voted and voted[2] is not None else 0], log
        return "new", dict(votes), None, log
    return "old", dict(votes), None, log


def votes_text(votes):
    return ", ".join("%s %d" % (k, votes.get(k, 0)) for k in ("new", "old", "same", "general", "none") if votes.get(k))


# ---- challenges

def alike(a, b):
    """Whether a candidate says the same as the old name: alike but for case and separators, or the same
    words (dread.naming words: pieces of three letters or more). Looser likeness (a vote's AGREE: half
    the words) would take a candidate that moves a name to another part (task_list_insert for
    port_list_insert) for a confirmation."""
    from dread import naming
    if spelled(a) == spelled(b):
        return True
    wa, wb = naming.words(a), naming.words(b)
    return bool(wa) and wa == wb


def challenge_function(provider, decider, namer, picture, n, reasons, samples=5, margin=MARGIN, tries=2):
    """One function's challenge (module docstring, 2): samples candidate questions (the first as it
    stands, the others with the callers in an order of their own, as a naming vote's), the vote, and
    unless the candidate agrees with the old name the pairwise decision. Returns {"outcome", "old",
    "candidate", "agreement", "samples", "votes", "name", "what", "form", "question" (the naming
    question's digest), "log"}: outcome one of OUTCOMES, name and what those the function has after it
    (a person's name stays: "flagged" where the decision went against it)."""
    from dread import naming, pilot
    program = picture.program
    fn = program.by_name[n]
    old = (picture.name(n), picture.what(n))
    first, form, plain = candidate_question(namer, picture, n, reasons)
    log = ["# %s (%s), challenged\n\nReasons: %s" % (n, old[0], reasons_text(reasons))]
    got = []
    for k in range(max(1, samples)):
        if k:
            seed = "%x:%d" % (fn.entry, k)
            user = namer.question(fn, picture.shown_as(), shuffle=seed)[0] + first[len(plain):]
        else:
            user = first
        proposed, what, part, why = pilot.ask_name(provider, program, n, user, tries, own=n)
        if samples > 1:
            log.append("## Candidate question %d of %d" % (k + 1, samples))
        log += part
        got.append((proposed, what))
    result = {"old": old, "form": form, "question": pilot.question_key(plain), "log": log, "votes": {},
              "candidate": None, "agreement": 0, "samples": " ".join(g[0] or "-" for g in got)}
    voted = naming.vote([g[0] for g in got])
    if voted is None:
        result.update(outcome="no candidate", name=old[0], what=old[1])
        return result
    candidate, agreement, index = voted
    result.update(candidate=(candidate, got[index][1]), agreement=agreement)
    if alike(candidate, old[0]):
        result.update(outcome="confirmed", name=old[0], what=old[1])
        return result
    outcome, votes, general, part = decide(decider, plain, old, (candidate, got[index][1]), margin, tries=tries)
    log += ["## Decision"] + part
    result["votes"] = votes
    if outcome == "general" and pilot.name_problem(program, general[0]) is not None:
        log.append("## The general name cannot be used: %s" % pilot.name_problem(program, general[0]))
        outcome = "old"
    person = picture.sources.get(n) == "person"
    if outcome == "old":
        result.update(outcome="kept", name=old[0], what=old[1])
    elif person:
        result.update(outcome="flagged", name=old[0], what=old[1],
                      flagged=(candidate, got[index][1]) if outcome == "new" else tuple(general))
    elif outcome == "new":
        result.update(outcome="changed", name=candidate, what=got[index][1])
    else:
        result.update(outcome="general", name=general[0], what=general[1])
    return result


def challenge_group(provider, decider, namer, picture, key, reasons, samples=5, margin=MARGIN, tries=2):
    """One group's challenge: samples questions for its name, prefix and sentence (the --task
    subsystems question with GROUP_NOTE), the vote over the prefix, and unless the candidate's prefix
    agrees with the old one the pairwise decision (GROUP_DECIDE_SYSTEM). Returns as
    challenge_function, name and what the group's (name, prefix, what) after it."""
    from dread import naming, pilot
    row = picture.subsystems[key]
    old = (row["name"], row["prefix"], row["what"])
    group = picture.members[key]
    plain = pilot.subsystem_question(namer, group, picture.touched, picture.shown_as())
    user = plain + "\n\n" + GROUP_NOTE.format(name=old[0], prefix=old[1], what=old[2], reasons=note(reasons))
    log = ["# Group %s (%s, prefix %s), challenged\n\nReasons: %s" % (picture.number.get(key, "?"), old[0], old[1],
                                                                       reasons_text(reasons))]
    got = []
    for k in range(max(1, samples)):
        parsed, part, why = pilot.ask_subsystem(provider, user, tries)
        if samples > 1:
            log.append("## Candidate question %d of %d" % (k + 1, samples))
        log += part
        got.append(parsed)
    result = {"old": old, "form": "group", "question": pilot.question_key(plain), "log": log, "votes": {},
              "candidate": None, "agreement": 0, "samples": " ".join(g[1] if g else "-" for g in got)}
    voted = naming.vote([g[1] if g else None for g in got])
    if voted is None:
        result.update(outcome="no candidate", name=old)
        return result
    prefix, agreement, index = voted
    candidate = got[index]
    result.update(candidate=candidate, agreement=agreement)
    if alike(candidate[1], old[1]):
        result.update(outcome="confirmed", name=old)
        return result
    outcome, votes, general, part = decide(decider, plain, old, candidate, margin, group=True, tries=tries)
    log += ["## Decision"] + part
    result["votes"] = votes
    if outcome == "general" and pilot.subsystem_problem(general[1]) is not None:
        log.append("## The general prefix cannot be used: %s" % pilot.subsystem_problem(general[1]))
        outcome = "old"
    if outcome == "old":
        result.update(outcome="kept", name=old)
    elif outcome == "new":
        result.update(outcome="changed", name=candidate)
    else:
        result.update(outcome="general", name=tuple(general))
    return result


# ---- the review

def review_path(target, image):
    return os.path.join(target.build, "pilot", image.name, "review.tsv")


def read_review(path):
    """The rows of a review.tsv as {column: text} (REVIEW_COLUMNS; a column a file lacks empty)."""
    from dread import pilot
    return pilot.read_table(path, REVIEW_COLUMNS)


def keep_review(target, image, values):
    """A line of review.tsv (REVIEW_COLUMNS) from values, empty where not given; tabs and line breaks
    in a value become spaces."""
    path = review_path(target, image)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write("\t".join(REVIEW_COLUMNS) + "\n")
    row = {k: " ".join(str(values.get(k, "")).split()) for k in REVIEW_COLUMNS}
    with open(path, "a") as f:
        f.write("\t".join(row[k] for k in REVIEW_COLUMNS) + "\n")
    return row


def write_conversation(target, image, name, log):
    folder = os.path.join(target.build, "pilot", image.name, "review")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name + ".md"), "a") as f:
        f.write("\n\n".join(log) + "\n\n")


def fresh(name, reasons, before):
    """Whether a name with these reasons is challenged again: never challenged (before None), or the last
    challenge (before: (the name it left, its reason_keys)) left another name or lacked one of the
    reasons."""
    return before is None or before[0] != name or bool(reason_keys(reasons) - before[1])


def group_label(row):
    return "%s (%s)" % (row.get("name", ""), row.get("prefix", ""))


def history(target, image):
    """{("function", name in the tables) or ("group", members key): (the name the last challenge left,
    its reason_keys)} of review.tsv: what earlier reviews challenged."""
    out = {}
    for r in read_review(review_path(target, image)):
        key = ("group", r["group"]) if r["kind"] == "group" else ("function", r["now"])
        out[key] = (r["name"], set(r["reason_keys"].split()))
    return out


def review(target, image, program, make_provider, make_decider=None, flow=None, names=None, jobs=4,
           samples=5, margin=MARGIN, rounds=ROUNDS, limit=None, threshold=THRESHOLD,
           group_threshold=GROUP_THRESHOLD, weights=None, device="", tries=2, dry_run=False, out=print):
    """The review (module docstring): rounds of challenges, groups first, jobs at a time (each thread
    with providers of its own: make_provider() for the candidates, make_decider() for the decisions,
    default the same), the changes written as they are decided, in the order of the ranking (the same
    answers give the same files). names: only these functions are challenged (their groups too).
    dry_run: the ranking printed, nothing asked or written. Returns the totals: {"functions": {outcome:
    count}, "groups": {...}, "rounds", "spent": {"namer", "decider"}}."""
    import concurrent.futures
    import threading
    from dread import pilot
    make_decider = make_decider or make_provider
    picture = Picture(target, image, program, flow)
    wanted = [n for n in (names if names is not None else [fn.name for fn in program.functions])
              if pilot.may_show(target, n)]
    wanted_set = set(wanted)
    totals = {"functions": collections.Counter(), "groups": collections.Counter(), "rounds": 0}
    if dry_run:
        functions, groups = ranked(picture, wanted, weights)
        for score, key, reasons in groups:
            row = picture.subsystems[key]
            mark = "challenged" if score >= group_threshold else "below"
            out("group %-4s %-40s %5.2f %s: %s" % (picture.number.get(key, "?"), "%s (%s)" % (row["name"][:28],
                                                                                           row["prefix"]), score,
                                                  mark, reasons_text(reasons)))
        over = [x for x in functions if x[0] >= threshold]
        for score, n, reasons in functions:
            out("%-24s %-36s %5.2f %s: %s" % (n, picture.name(n), score, "challenged" if score >= threshold
                                              else "below", reasons_text(reasons)))
        out("dread pilot --task review --dry-run: %d of %d groups and %d of %d named functions would be challenged "
            "(%d with a reason; threshold %.2f, groups %.2f)%s; nothing asked" % (
                sum(1 for x in groups if x[0] >= group_threshold), len(picture.subsystems),
                len(over[:limit] if limit else over), len([n for n in wanted if picture.sources.get(n)]),
                len(functions), threshold, group_threshold, " (at most %d a round)" % limit if limit else ""))
        totals["would"] = {"groups": sum(1 for x in groups if x[0] >= group_threshold),
                           "functions": len(over[:limit] if limit else over), "with_reason": len(functions)}
        return totals
    from dread.pilot import Namer
    namer = Namer(target, image, program, flow, device=device)
    namer.subsystem = picture.subsystem_lines()
    namer.prepare_code([fn.name for fn in program.functions if pilot.may_show(target, fn.name)])
    number = 1 + max((int(r["review"]) for r in read_review(review_path(target, image)) if r["review"].isdigit()),
                     default=0)
    local, made, lock = threading.local(), {"namer": [], "decider": []}, threading.Lock()

    def providers():
        if getattr(local, "pair", None) is None:
            namer_p = make_provider()
            decider_p = make_decider() if make_decider is not make_provider else namer_p
            local.pair = (namer_p, decider_p)
            with lock:
                made["namer"].append(namer_p)
                if decider_p is not namer_p:
                    made["decider"].append(decider_p)
        return local.pair

    before, changed, renamed = history(target, image), set(), {}

    def run(jobs_list):
        def one(job):
            kind, key, reasons = job
            p, d = providers()
            if kind == "group":
                return challenge_group(p, d, namer, picture, key, reasons, samples, margin, tries), p, d
            return challenge_function(p, d, namer, picture, key, reasons, samples, margin, tries), p, d
        if not jobs_list:
            return []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            return list(pool.map(one, jobs_list))

    for round_ in range(1, max(1, rounds) + 1):
        _, groups = ranked(picture, wanted, weights, renamed)
        todo = [("group", key, reasons) for score, key, reasons in groups
                if score >= group_threshold and ("group", key) not in changed
                and fresh(group_label(picture.subsystems[key]), reasons, before.get(("group", key)))
                and any(n in wanted_set for n in picture.members[key])]
        asked = 0
        for (kind, key, reasons), (result, p, d) in zip(todo, run(todo)):
            asked += 1
            row = picture.subsystems[key]
            first = program.by_name[picture.members[key][0]]
            write_conversation(target, image, "group-%s" % picture.number.get(key, "x"), result["log"])
            new = result["name"]
            keep_review(target, image, {
                "review": number, "round": round_, "kind": "group", "address": "0x%x" % first.entry,
                "now": "group %s" % picture.number.get(key, ""), "group": key, "source": "proposal",
                "score": "%.2f" % score_of(reasons), "reasons": reasons_text(reasons),
                "old": "%s (%s)" % (row["name"], row["prefix"]), "old_what": row["what"],
                "candidate": "%s (%s)" % result["candidate"][:2] if result["candidate"] else "",
                "candidate_what": result["candidate"][2] if result["candidate"] else "",
                "agreement": "%d of %d" % (result["agreement"], samples) if result["candidate"] else "",
                "votes": votes_text(result["votes"]), "outcome": result["outcome"],
                "name": "%s (%s)" % new[:2], "what": new[2], "provider": p.label(), "decider": d.label(),
                "reason_keys": " ".join(sorted(reason_keys(reasons)))})
            before[("group", key)] = ("%s (%s)" % new[:2], reason_keys(reasons))
            totals["groups"][result["outcome"]] += 1
            out("group %-4s %s (%s) -> %s: %s%s" % (
                picture.number.get(key, ""), row["name"], row["prefix"],
                "%s (%s)" % result["candidate"][:2] if result["candidate"] else "no candidate", result["outcome"],
                " (%s)" % votes_text(result["votes"]) if result["votes"] else ""))
            if result["outcome"] in ("changed", "general"):
                values = {"group": picture.number.get(key, ""), "first": "0x%x" % first.entry,
                          "functions": len(picture.members[key]), "members": key, "name": new[0], "prefix": new[1],
                          "what": new[2], "provider": d.label() if result["outcome"] == "general" else p.label(),
                          "question": result["question"]}
                if result["outcome"] == "changed" and samples > 1:
                    values["confidence"] = "%.2f" % (result["agreement"] / samples)
                    values["samples"] = result["samples"]
                renamed[key] = (row, pilot.keep_subsystem(target, image, values))
                picture.subsystems[key] = renamed[key][1]
                changed.add(("group", key))
                namer.subsystem = picture.subsystem_lines()
        functions, _ = ranked(picture, wanted, weights, renamed)
        todo = [("function", n, reasons) for score, n, reasons in functions
                if score >= threshold and n not in changed and fresh(picture.name(n), reasons,
                                                                     before.get(("function", n)))]
        todo = todo[:limit] if limit else todo
        for (kind, n, reasons), (result, p, d) in zip(todo, run(todo)):
            asked += 1
            before[("function", n)] = (result["name"], reason_keys(reasons))
            fn = program.by_name[n]
            write_conversation(target, image, n, result["log"])
            outcome = result["outcome"]
            flagged = result.get("flagged")
            keep_review(target, image, {
                "review": number, "round": round_, "kind": "function", "address": "0x%x" % fn.entry, "now": n,
                "group": picture.group_of.get(n, ""), "source": picture.sources.get(n, ""),
                "score": "%.2f" % score_of(reasons), "reasons": reasons_text(reasons),
                "old": result["old"][0], "old_what": result["old"][1],
                "candidate": (flagged or result["candidate"] or ("",))[0],
                "candidate_what": (flagged or result["candidate"] or ("", ""))[1],
                "agreement": "%d of %d" % (result["agreement"], samples) if result["candidate"] else "",
                "votes": votes_text(result["votes"]), "outcome": outcome, "name": result["name"],
                "what": result["what"], "provider": p.label(), "decider": d.label(),
                "reason_keys": " ".join(sorted(reason_keys(reasons)))})
            totals["functions"][outcome] += 1
            out("%-24s %s -> %s: %s%s" % (n, result["old"][0], (flagged or result["candidate"] or ("no candidate",))[0],
                                          outcome, " (%s)" % votes_text(result["votes"]) if result["votes"] else ""))
            if outcome in ("changed", "general"):
                more = {"pass": str(picture.passes + round_), "question": result["question"],
                        "names_shown": str(len(namer.names_shown(fn, picture.shown_as())))}
                if outcome == "changed" and samples > 1:
                    more["confidence"] = "%.2f" % (result["agreement"] / samples)
                    more["samples"] = result["samples"]
                row = pilot.keep_proposal(target, image, fn, result["name"], result["what"],
                                          d if outcome == "general" else p, result["form"] + REVIEWED, more)
                row["entry"] = fn.entry
                picture.rows[fn.entry] = row
                picture.set_name(n, result["name"], " ".join(result["what"].split()))
                changed.add(n)
        if not asked:
            break
        if all(r["outcome"] == "no candidate" for r in read_review(review_path(target, image))
               if r["review"] == str(number) and r["round"] == str(round_)):
            out("dread pilot --task review: no challenge of round %d got a candidate (the provider?); no more "
                "rounds" % round_)
            totals["rounds"] = round_
            break
        totals["rounds"] = round_
    totals["spent"] = {k: pilot.summed(p.spent() for p in v) for k, v in made.items()}
    return totals
