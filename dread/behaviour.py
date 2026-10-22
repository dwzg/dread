"""dread.behaviour: what a function does when it runs, told to a reader (dread pilot --task name
--behaviour shows it beside the function's code).

A name says what a function does; its code says how. The image's cases run the original function in
the emulator (dread.emu), on the original image alone, its callees for real except those a case
stubs, and each run is observed:

- the arguments the function reads (dread.regflow), with the text one points to where the run reads
  it as text (a byte at a time; an argument the search took from the function's constants may hold a
  string's address without being one);
- its result where its callers read one (the result register shown by its name for a function
  nobody calls directly: a handler, a task, a callback, whose callers are not known);
- the functions it calls itself (not those its callees call), in order, with the arguments each one
  reads, the text one points to (unless it is an argument's whose text was not shown), and the
  result where that callee's callers read one; a call through a pointer to the harness's stub is "a
  function pointer";
- the device register accesses its own instructions make, in order (dread.emu logs every access; a
  callee's show as the call), a wait on one (a poll stage that ended it), a system call;
- what its own instructions wrote outside the stack, named as readable C names memory at a fixed
  address (g_ADDRESS, io_ADDRESS, rom_ADDRESS, a suffix for a byte or a half-word; the
  readable_names table's globals where it names them, dread.naming), and memory in a scratch buffer
  an input pointed to after that input: *(r0 + 8) behind the argument r0, *(g_20000a4c + 4) behind a
  pointer the input put into memory (offsets in decimal, as the readable C writes them). A value that
  points into such a buffer is written so too (r0 + 1), one into the stack as &local; the bytes it
  reads from such a buffer one at a time (text, data: what a string or buffer function consumes);
- how it ended: a return, a call of a function that never returns, a call the case ended the run at
  (a callee a search's run had hung in), the instruction limit (an endless loop, a wait nothing
  ended), and how often its most-run block ran (a loop's rounds).

The cases are the function's written ones (extra_cases), its kept ones and its generated ones as
dread.autocase cached them (BUILD/autocases/IMAGE/NAME.json) whatever their key: they are inputs
only, run anew on the image as it is, so a stale one shows what this code does with it. At most
RUN_CASES of them run (spread evenly over the list), and a run that faults is left out. Of the rest,
CASES are shown (choose): first the one that returns and runs most of the function, then each time
the one that runs most of what the shown ones did not, or ends otherwise, calls other functions,
touches other device registers or returns another kind of result (0, 1, -1, negative, small,
other); at most CHARS characters in all. A function without any case gets them from a search stopped
after QUICK_SECONDS (quick_cases): on FreeRTOS's GCC -Os build without any cached case, 134 s for its
366 functions on 8 workers (a busy machine), 20 s once they are kept, and 271 of 358 functions' runs
read as with the lift check's cases.

Measured on FreeRTOS's GCC -Os build made nameless (docs/todo.md, Function names): with the notes
of the readable C showing a long text's end (an assertion's source file), the runs added nothing
measurable to the names Haiku proposed, for 37 % more input tokens; an option, to be tried where
the code says less (drivers with unnamed device registers).

The inputs are a search's (random values steered to reach every branch, pointers aimed at scratch
buffers), not the program's own: HEADER says so. Not shown: memory the function only reads, the
values an input gave memory, what its callees write, and a call of the function to itself (it shows
as a round of a loop). A run's callees run for real, so a callee that fails on a generated input
makes the run show that failure. Deterministic: the same image, cases and tables give the same text.
"""
import os
import re

from unicorn import UC_HOOK_BLOCK, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE

from dread.emu import ARGS_ROOM

CASES = 3               # runs shown per function at most
RUN_CASES = 24          # cases run per function to choose from at most (spread evenly over the list)
CHARS = 1500            # characters of a function's runs at most (the first run is cut to fit)
ITEMS = 8               # events (calls, device accesses) and memory writes shown per run at most, each
TEXT = 40               # characters of a text an argument points to at most (a longer one: its start and its
                        # end, where a path has its file name: an assertion's __FILE__ tells the subsystem)
SCAN = 256              # bytes looked at for the end of a text
BYTES = 12              # bytes of a longer write shown
REACH = 0x400           # an address this far behind a pointer an input aimed at the scratch is named after it
QUICK_SECONDS = 5       # the search for a function without cases stops after this long (quick_cases)

HEADER = ("Runs of its machine code in an emulator on inputs a search generated (random values that reach its "
          "branches, pointers into scratch buffers), callees running too unless stubbed (they return what the "
          "input says); what its own instructions access, memory named as in the C, *({reg} + 8) behind the "
          "pointer an input gave in {reg}:")


def cases_of(target, image, name):
    """The cases of a function to run: its written ones, its kept ones and its cached generated ones
    (whatever their key: inputs only)."""
    from dread import autocase
    out = list(autocase.extra_load(image, name)) + list(autocase.kept_load(image, name))
    cached = autocase.cache_load(os.path.join(target.build, "autocases", image.name, name + ".json"))
    if cached:
        out += [c for c in cached.get("cases") or () if isinstance(c, dict)]
    return [c for c in out if isinstance(c.get("args"), list)]


def quick_cases(gen, fn, search=True):
    """Cases for a function that has none: dread.autocase's search (loop cases for a function that
    never returns, as dread lift --check makes them) stopped after QUICK_SECONDS, as dread sysrecord
    does for a function without cached cases. What it finds within that time depends on the machine's
    speed; the cases are kept in BUILD/behaviour/IMAGE/NAME.json under the key of the function's
    bytes and the image (dread.autocase.case_key), so a run started again shows the same. A full
    generation (dread lift --check) finds more and is used instead when it has run. search=False:
    only the kept ones ([] without)."""
    from dread import autocase
    loop = fn.name in gen.endless()
    frame = gen.loop_frame(fn.name) if loop else 0
    options = {"loop": True} if loop else {}
    key, calls = autocase.case_key(gen, fn.name, fn, dict(options, behaviour=QUICK_SECONDS), [], frame)
    path = os.path.join(gen.target.build, "behaviour", gen.image.name, fn.name + ".json")
    cached = autocase.cache_load(path)
    if cached and cached.get("key") == key:
        return cached.get("cases") or []
    if not search:
        return []
    try:
        cases = gen.generate(fn.name, fn.entry, fn.insns, calls=calls, frame=frame, seconds=QUICK_SECONDS, loop=loop)
    except autocase.GenerationError:
        cases = []
    autocase.cache_store(path, {"key": key, "cases": cases, "seconds": QUICK_SECONDS})
    return cases


def spread(cases, most=RUN_CASES):
    """At most `most` of the cases, spread evenly over the list (the generator's later cases reach
    the branches its first ones did not)."""
    if len(cases) <= most:
        return list(cases)
    return [cases[k * len(cases) // most] for k in range(most)]


def result_regs(program, flow, name):
    """The registers of a function's result its callers read ([] when they read none; the first
    result register when dread.regflow is not there)."""
    regs = list(program.target.abi.result)
    if not flow:
        return regs[:1]
    live = set(flow.kept_regs(name)) - set(flow.preserved_regs(name))
    return [r for r in regs if r in live]


def arg_count(program, flow, name):
    """How many argument registers a function reads (dread.regflow; all of them without it)."""
    args = list(program.target.abi.arg_regs)
    if not flow:
        return len(args)
    reads = set(flow.read_regs(name))
    return max((k + 1 for k, r in enumerate(args) if r in reads), default=0)


def printable(b):
    return 0x20 <= b < 0x7F or b in (9, 10, 13)


def quoted(data, limit=TEXT):
    """Bytes as a quoted C text; a longer one than limit as its first third and its end."""
    def esc(part):
        return bytes(part).decode("ascii").encode("unicode_escape").decode("ascii").replace('"', '\\"')
    if len(data) <= limit:
        return '"%s"' % esc(data)
    head = limit // 3
    return '"%s...%s"' % (esc(data[:head]), esc(data[len(data) - (limit - head):]))


def text_at(m, address, limit=TEXT):
    """The printable NUL-terminated text (two characters at least) at an address of the machine's
    memory, quoted (quoted()), or None."""
    if address < 0x100:
        return None
    data = None
    for n in (SCAN, 64, 16, 4):             # (memory may end behind the text)
        try:
            data = m.read(address, n)
            break
        except Exception:
            continue
    if data is None:
        return None
    end = data.find(b"\0")
    if end < 2 or not all(printable(b) for b in data[:end]):
        return None
    return quoted(data[:end], limit)


def number(v):
    return "%d" % v if v < 10 else "0x%x" % v


CTYPES = {1: "u8", 2: "u16", 4: "u32", 8: "u64"}


class Places:
    """Names of addresses and pointers as a run's lines show them: behind a pointer an input aimed at
    the scratch (*(r0 + 8)), else as readable C names memory at a fixed address; a pointer into the
    stack is &local."""

    def __init__(self, target, layout, case, nargs, named=None):
        from dread import naming
        self.memory, self.layout, self.named, self.naming = target.memory, layout, named or {}, naming
        lo = target.memory.scratch_base
        hi = lo + target.memory.scratch_size if lo is not None else None
        self.bases = []                     # [(address, expression)]

        def scratch(v):
            return lo is not None and lo <= v < hi
        regs = list(target.abi.arg_regs)
        for k, v in enumerate(list(case.get("args", ()))[:nargs]):
            if scratch(v & 0xFFFFFFFF) and all(b != v & 0xFFFFFFFF for b, _ in self.bases):
                self.bases.append((v & 0xFFFFFFFF, regs[k]))
        cells = sorted((a, v & 0xFFFFFFFF) for a, n, v in case.get("mem", ()) if n == 4 and scratch(v & 0xFFFFFFFF))
        for _ in range(3):                  # (a pointer behind a pointer: once its cell has a name)
            known = {b for b, _ in self.bases}
            for a, v in cells:
                if v not in known:
                    self.bases.append((v, self.name(a, "u32")))
                    known.add(v)

    def base_of(self, address):
        best = None
        for base, expr in self.bases:
            if base <= address < base + REACH and (best is None or base > best[0]):
                best = (base, expr)
        return best

    def name(self, address, ctype="u32"):
        """address as an expression: *(base + offset) behind a known pointer; a stack argument as the
        readable C's parameter (s0 the first word above the stack top: dread.lift); else the global's
        name (the readable_names table's, or g_/io_/rom_ and the address)."""
        best = self.base_of(address)
        if best is not None:
            base, expr = best
            return "*(%s + %d)" % (expr, address - base) if address > base else "*%s" % expr
        top, word = self.layout.stack_top, self.layout.word
        if top <= address < top + ARGS_ROOM and (address - top) % word == 0:
            return "s%d" % ((address - top) // word)
        entry = self.named.get(self.naming.global_key(address, ctype))
        named = entry.get("name") if isinstance(entry, dict) else None
        return named or self.naming.default_name(address, ctype, self.memory)

    def value(self, v):
        """A value as a run's line shows it: into an input's buffer as that input plus an offset, into
        the stack as &local, else the number."""
        best = self.base_of(v)
        if best is not None:
            base, expr = best
            return "%s + %d" % (expr, v - base) if v > base else expr
        if self.layout.in_stack(v):
            return "&local"
        return number(v)


def observe(gen, fn, case, flow=None, named=None, index=0, has_callers=True):
    """One run of a case on the original image, observed (see the module's docstring): a dict, or None
    when the run faults."""
    from dread.autocase import cover_span
    program, lay, target = gen.program, gen.layout, gen.target
    m = gen.machine()
    m.plan, m.replay, m.function = [], False, fn.name
    try:
        gen.apply(m, case)
    except Exception:
        return None
    lo, hi = cover_span(program, fn.insns)
    entries = program.by_entry
    nargs = arg_count(program, flow, fn.name)
    places = Places(target, lay, case, nargs, named)
    args = [a & 0xFFFFFFFF for a in list(case.get("args", ()))[:nargs]]
    texts = [text_at(m, a) for a in args]
    calls, counts, pending, sizes, marks, reads, bytewise = [], {}, [], {}, [], {}, set()
    state = {"inside": False}
    sp_id, arg_ids = lay.sp, lay.arg_regs
    io = target.memory.is_io
    log = m.log

    def own_log(*event):                    # (whether the function's own code made the access)
        marks.append(state["inside"])
        log(*event)
    m.log = own_log

    def on_block(uc, address, size, user):
        inside = lo <= address < hi
        if state["inside"] and not inside and address != lay.sentinel:
            callee = entries.get(address)
            if callee is not None:
                n, regs, what = arg_count(program, flow, callee.name), result_regs(program, flow, callee.name), \
                    callee.name
            else:
                owner = program.function_at(address)
                n, regs = len(arg_ids), list(target.abi.result)[:1]
                what = ("a function pointer" if address in m.stubs else
                        "%s+0x%x" % (owner.name, address - owner.entry) if owner is not None else "0x%x" % address)
            values = [uc.reg_read(r) & 0xFFFFFFFF for r in arg_ids[:n]]
            calls.append({"to": what, "args": values, "texts": [text_at(m, v) for v in values], "result": regs,
                          "ret": None, "at": len(m.calls), "stubbed": address in m.stubs})
            pending.append((len(calls) - 1, uc.reg_read(sp_id)))
        if inside:
            counts[address] = counts.get(address, 0) + 1
            if pending:
                k, at = pending[-1]
                if uc.reg_read(sp_id) >= at:            # (back from the call: its result)
                    pending.pop()
                    if calls[k]["result"]:
                        calls[k]["ret"] = [m.reg(r) & 0xFFFFFFFF for r in calls[k]["result"]]
        state["inside"] = inside

    def on_write(uc, access, address, size, value, user):
        if state["inside"] and not lay.in_stack(address) and not io(address):
            sizes[address] = size
            for a in range(address + 1, address + size):
                sizes[a] = 0                # (inside a store that began before)

    def on_read(uc, access, address, size, value, user):
        if size == 1:
            bytewise.add(address)
            if state["inside"] and address not in reads and places.base_of(address) is not None:
                reads[address] = uc.mem_read(address, 1)[0]    # (the input's byte: the first read of it)

    m.hook_add(UC_HOOK_BLOCK, on_block)
    m.hook_add(UC_HOOK_MEM_WRITE, on_write)
    m.hook_add(UC_HOOK_MEM_READ, on_read)   # (bytes read one at a time: text, data)
    m.cover(lo, hi)
    try:
        result = m.call(fn.entry, *case["args"], max_instructions=case.get("limit", 200_000))
    except Exception:
        return None
    finally:
        m.log = log
    if m.stopped_at == "never returns":
        ending = "stopped"
    elif m.stopped_at == "calls":
        ending = "calls"
    elif result.calls and result.calls[-1] == ("did not return",):
        ending = "endless" if m.self_loop is not None else "limit"
    else:
        ending = "returned"
    ret, regs = None, result_regs(program, flow, fn.name)
    if ending == "returned":
        if regs:
            ret = [m.reg(r) & 0xFFFFFFFF for r in regs]
        elif not has_callers:               # (who calls it is not known: whether it has a result neither)
            ret = [m.reg(target.abi.result[0]) & 0xFFFFFFFF]
    stop = None
    if ending == "stopped":
        for event in reversed(result.calls):
            if isinstance(event[0], str) and event[0].endswith("never returns"):
                stop = int(event[0].split()[1].rstrip(","), 16)
                break
    own = {e[1] for i, e in enumerate(result.calls[:len(marks)]) if marks[i] and e and e[0] == "io read"}
    kept = [(i, e) for i, e in enumerate(result.calls)
            if e and e[0] in ("io read", "io write", "svc") and i < len(marks) and marks[i]
            or e and e[0] == "io poll reads" and e[1] in own]      # (counted after the run: its reads say whose)
    # The text an argument points to is shown when the run read it as text (its first byte as a
    # byte, here or in a callee): the generator puts texts into buffers it learned, structures too,
    # and takes argument values from the function's constants, string addresses among them (a tick
    # count of 0x1065 showed as a source file's path). A value the function only passes on from such
    # an argument does not show its text in the calls either.
    texts = [t if t is None or a in bytewise else None for a, t in zip(args, texts)]
    hidden = {a for a, t in zip(args, texts) if t is None}
    for c in calls:
        c["texts"] = [None if v in hidden else t for v, t in zip(c["args"], c["texts"])]
    run = {"index": index, "args": args, "texts": texts, "ret": ret, "result_named": bool(regs), "ending": ending,
           "calls": calls, "log": [e for _, e in kept], "log_at": [i for i, _ in kept],
           "writes": writes_of(result.ranges, sizes), "reads": sorted(reads.items()),
           "rounds": max(counts.values(), default=0),
           "covered": m.covered_insns(fn.insns), "stop": stop, "places": places}
    run["signature"] = (ending, tuple(dict.fromkeys(c["to"] for c in calls)),
                        tuple(sorted({(e[0], e[1]) for e in run["log"] if e[0] != "svc"})),
                        None if ret is None else kind_of_result(ret[0]))
    return run


def kind_of_result(v):
    """What kind of result a value is, for telling runs apart: 0, 1, -1, another negative number, a
    small one, any other."""
    if v in (0, 1, 0xFFFFFFFF):
        return v
    return "negative" if v & 0x80000000 else "small" if v < 0x100 else "other"


def writes_of(ranges, sizes):
    """[(address, size, bytes)] of the function's own stores (sizes: {address: store size, 0 inside a
    store}) in the memory a run wrote outside the stack (ranges: the values at its end)."""
    out = []
    for start, data in ranges:
        a, end = start, start + len(data)
        while a < end:
            n = sizes.get(a)
            if not n:
                a += 1
                continue
            n = min(n, end - a)
            out.append((a, n, bytes(data[a - start:a - start + n])))
            a += n
    return out


def bytes_text(data):
    stripped = data.rstrip(b"\0")
    if len(stripped) >= 2 and all(printable(b) for b in stripped):
        return quoted(stripped) + (" and %d zero bytes" % (len(data) - len(stripped)) if len(data) - len(stripped) > 1
                                   else "")
    if len(set(data)) == 1:
        return "%d bytes of 0x%02x" % (len(data), data[0])
    return "%s%s" % (" ".join("%02x" % b for b in data[:BYTES]), " ..." if len(data) > BYTES else "")


def memory_items(run, writes=None):
    """The run's own writes (or `writes`: [(address, size, bytes)]) as items: name = value; a stretch
    of stores of one size, three or more, or two bytes or more, as one item: bytes as a text or in
    hex, other stores as a list of their values."""
    places = run["places"]
    writes = run["writes"] if writes is None else writes
    items, k = [], 0
    while k < len(writes):
        j = k
        while j + 1 < len(writes) and writes[j + 1][0] == writes[j][0] + writes[j][1] and \
                writes[j + 1][1] == writes[k][1]:
            j += 1
        stretch = writes[k:j + 1]           # (stores of one size, one after another)
        if len(stretch) >= 2 and all(n == 1 for _, n, _ in stretch):
            data = b"".join(w[2] for w in stretch)
            items.append("%s.. (%d bytes) = %s" % (places.name(stretch[0][0], "u8"), len(data), bytes_text(data)))
        elif len(stretch) >= 3:
            values = [places.value(int.from_bytes(d, "little")) if n == 4 else number(int.from_bytes(d, "little"))
                      for _, n, d in stretch]
            items.append("%s.. (%d stores) = %s%s" % (places.name(stretch[0][0], CTYPES.get(stretch[0][1], "u8")),
                                                     len(stretch), ", ".join(values[:BYTES // 2]),
                                                     ", ..." if len(values) > BYTES // 2 else ""))
        else:
            for a, n, data in stretch:
                v = int.from_bytes(data, "little")
                items.append("%s = %s" % (places.name(a, CTYPES.get(n, "u8")),
                                          places.value(v) if n == 4 else number(v)))
        k = j + 1
    return items


def listed(values, places, most=6):
    """Values one after another: a text when they are all printable characters (three or more), else
    the first `most` of them."""
    if len(values) >= 3 and all(v < 0x80 and printable(v) for v in values):
        return quoted(bytes(values))
    shown = ", ".join(places.value(v) for v in values[:most])
    return shown + (", ..." if len(values) > most else "")


def call_text(c, places, vary=None):
    """A call as a line shows it; vary: the argument shown as x (a folded pattern, events), "ret" its
    result. A text an argument points to is shown unless it lies in an input's buffer (the input's
    text, shown with the run's arguments, or made by the function from it)."""
    shown = []
    for k, (v, t) in enumerate(zip(c["args"], c["texts"])):
        t = t if places.base_of(v) is None else None
        shown.append("x" if k == vary else "%s %s" % (places.value(v), t) if t else places.value(v))
    text = "%s(%s)" % (c["to"], ", ".join(shown))
    if c["stubbed"]:
        text += " stubbed"
    if c["ret"] is not None:
        text += " = x" if vary == "ret" else " = %s" % ":".join(places.value(v) for v in reversed(c["ret"]))
    return text


def io_text(event, places, vary=False):
    kind, address = event[0], event[1]
    if kind == "svc":
        return "svc"
    if kind == "io poll reads":
        return "read %s %d times (a wait)" % (places.name(address, "u32"), event[2])
    name = places.name(address, CTYPES.get(event[2], "u32"))
    value = "x" if vary else number(event[3])
    return "read %s = %s" % (name, value) if kind == "io read" else "%s = %s" % (name, value)


def text_of(item, places):
    return call_text(item, places) if isinstance(item, dict) else io_text(item, places)


def slots(item, places):
    """{template: value} of the ways one value of an event can vary while the rest stays: a device
    access's value, a call's one argument (no texts among them) or its result."""
    if isinstance(item, tuple):
        return {io_text(item, places, True): item[3]} if item[0] in ("io read", "io write") else {}
    out = {}
    if not any(t for v, t in zip(item["args"], item["texts"]) if places.base_of(v) is None):
        for k, v in enumerate(item["args"]):
            out[call_text(item, places, k)] = v
    if item["ret"] is not None and len(item["ret"]) == 1:
        out[call_text(item, places, "ret")] = item["ret"][0]
    return out


FOLD_PERIODS = (1, 2, 3)    # events a repeating pattern has (a poll and a write, a lock, a call, an unlock)
FOLD_MIN = 3                # repetitions a pattern needs to be folded


def fold_at(seq, i, places):
    """(events taken, text) of the longest repeating pattern at seq[i]: FOLD_MIN repetitions or more of
    FOLD_PERIODS events each, alike but for at most one value (shown as x and listed after), or None."""
    best = None
    for p in FOLD_PERIODS:
        texts = [text_of(item, places) for item in seq[i:i + p]]
        if len(texts) < p:
            break
        n, vary, values = 1, None, None
        while i + (n + 1) * p <= len(seq):
            group = seq[i + n * p:i + (n + 1) * p]
            diff = [j for j in range(p) if text_of(group[j], places) != texts[j]]
            if not diff:
                if values is not None:
                    values.append(slots(group[vary[0]], places).get(vary[1]))
                n += 1
                continue
            if len(diff) > 1 or (vary is not None and diff[0] != vary[0]):
                break
            j = diff[0]
            if vary is None:
                common = set(slots(seq[i + j], places)) & set(slots(group[j], places))
                if not common:
                    break
                template = sorted(common)[0]
                first = [slots(seq[i + j + t * p], places).get(template) for t in range(n)]
                if any(v is None for v in first):
                    break
                vary, values = (j, template), first
            got = slots(group[j], places).get(vary[1])
            if got is None:
                break
            values.append(got)
            n += 1
        if n >= FOLD_MIN and (best is None or n * p > best[0]):
            parts = list(texts)
            if vary is not None:
                parts[vary[0]] = vary[1]
            body = parts[0] if p == 1 else "(%s)" % "; ".join(parts)
            text = "%s (%d times)" % (body, n) + (", x = %s" % listed(values, places) if vary is not None else "")
            best = (n * p, text)
    return best


def events(run):
    """The run's calls and its own device accesses in order; a pattern that repeats (fold_at) as one
    item, two alike in a row as one (2 times)."""
    places, seq, calls, k = run["places"], [], run["calls"], 0
    for i, event in enumerate(run["log"] + [None]):      # (a call's "at" is the log's length when it began)
        while k < len(calls) and (event is None or calls[k]["at"] <= run["log_at"][i]):
            seq.append(calls[k])
            k += 1
        if event is not None:
            seq.append(event)
    out, i = [], 0
    while i < len(seq):
        folded = fold_at(seq, i, places)
        if folded:
            out.append(folded[1])
            i += folded[0]
            continue
        text = text_of(seq[i], places)
        if i + 1 < len(seq) and text_of(seq[i + 1], places) == text:
            out.append(text + " (2 times)")
            i += 2
            continue
        out.append(text)
        i += 1
    return out


ENDINGS = {"limit": "ran to the instruction limit (a loop or a wait that did not end)",
           "endless": "ended in an endless loop", "calls": "was stopped after many calls (a loop that keeps calling)"}


def describe(run, name, program, endless=(), number_of=None):
    """The lines of one run."""
    regs, places = list(program.target.abi.arg_regs), run["places"]
    shown = []
    for k, (v, t) in enumerate(zip(run["args"], run["texts"])):
        shown.append("%s=%s%s" % (regs[k], number(v), " " + t if t else ""))
    head = "%s(%s)" % (name, ", ".join(shown))
    if run["ending"] == "returned":
        ret = ":".join(places.value(v) for v in reversed(run["ret"])) if run["ret"] else ""
        head += " returned" + (" %s" % ret if run["result_named"] else
                               " (%s = %s)" % (program.target.abi.result[0], ret) if ret else "")
    elif run["ending"] == "stopped":
        callee = program.by_entry.get(run["stop"]) if run["stop"] is not None else None
        callee = callee.name if callee else "a function"
        head += (" called %s, which never returns" % callee if callee in endless else
                 " was ended at its call of %s (the input made a run wait there)" % callee)
    else:
        head += " " + ENDINGS[run["ending"]]
    lines = ["%s%s" % ("%d. " % number_of if number_of else "", head)]
    seq = events(run)
    if seq:
        more = " ... and %d more" % (len(seq) - ITEMS) if len(seq) > ITEMS else ""
        lines.append("   does: %s%s" % ("; ".join(seq[:ITEMS]), more))
    reads = [item for item in memory_items(run, [(a, 1, bytes([v])) for a, v in run["reads"]])
             if not (item.split(" = ", 1)[-1].startswith('"') and item.split(" = ", 1)[-1] in head)]   # (shown)
    for what, items in (("reads", reads), ("writes", memory_items(run))):
        if items:
            more = " ... and %d more" % (len(items) - ITEMS) if len(items) > ITEMS else ""
            lines.append("   %s: %s%s" % (what, "; ".join(items[:ITEMS]), more))
    if run["rounds"] > 1:
        lines.append("   its most-run block ran %d times" % run["rounds"])
    return lines


def evidence(run):
    """What a run shows beyond its arguments and its ending: texts its arguments point to, bytes it
    read from an input's buffer, memory it wrote, calls and device accesses (one point each)."""
    return sum(1 for t in run["texts"] if t) + bool(run["reads"]) + bool(run["writes"]) + bool(run["calls"]) + \
        bool(run["log"])


def visible(run):
    """A run's lines without the counts of what they leave out: two runs that differ only there look
    alike."""
    return [re.sub(r" \.\.\. and \d+ more$", "", line) for line in run["lines"]]


def choose(runs, most=CASES):
    """The runs to show (see the module's docstring), in the order chosen: each time the one that
    runs most of what the chosen ones did not, or has another signature (ending, callees, device
    registers, the kind of result), one that returns before one that does not; on a tie the one that shows
    more (evidence), then the shorter one. A run whose lines are a chosen one's is not shown again."""
    chosen, covered, seen = [], set(), set()
    left = [r for r in runs]
    while left and len(chosen) < most:
        def score(r):
            new = len(r["covered"] - covered)
            fresh = r["signature"] not in seen
            return (new > 0 or fresh, r["ending"] == "returned", new, fresh, evidence(r), -r["size"], -r["index"])
        best = max(left, key=score)
        left.remove(best)
        if chosen and not best["covered"] - covered and best["signature"] in seen:
            break
        if any(visible(best) == visible(c) for c in chosen):
            continue
        chosen.append(best)
        covered |= best["covered"]
        seen.add(best["signature"])
    return chosen


def section(gen, fn, flow=None, named=None, cases=None, has_callers=True, make=True):
    """The text of a function's runs for a question (HEADER and its runs), or None without a case
    that runs. A function without cases of its own gets quick_cases (make=False: only those kept from
    an earlier search)."""
    program = gen.program
    try:
        endless = gen.endless()
    except Exception:
        endless = set()
    if cases is None:
        cases = cases_of(gen.target, gen.image, fn.name)
        if not cases:
            cases = quick_cases(gen, fn, search=make)
    runs = []
    for k, case in enumerate(spread(cases)):
        run = observe(gen, fn, case, flow, named, k, has_callers)
        if run is None:
            continue
        run["lines"] = describe(run, fn.name, program, endless)
        run["size"] = sum(len(line) + 1 for line in run["lines"])
        runs.append(run)
    if not runs:
        return None
    out, room = [HEADER.format(reg=list(gen.target.abi.arg_regs)[0])], CHARS
    for k, run in enumerate(choose(runs), 1):
        lines = describe(run, fn.name, program, endless, k)
        size = sum(len(line) + 1 for line in lines)
        if size > room:
            if k > 1:
                break
            for line in lines:
                if len(line) + 1 > room:
                    out.append(line[:max(0, room - 4)] + " ...")
                    break
                out.append(line)
                room -= len(line) + 1
            break
        out += lines
        room -= size
    return "\n".join(out)


_G = {}


def _section_one(name):
    fn = _G["program"].by_name.get(name)
    if fn is None:
        return None
    try:
        return section(_G["gen"], fn, _G["flow"], _G["named"], has_callers=name in _G["called"], make=_G["make"])
    except Exception:
        return None


def sections(target, image, program, flow, names, jobs=None, called=None, make=True):
    """{name: section} of the functions of names that have a case that runs, made in forked workers
    (dread.util.polite's workers(); in this process for a few). called: the names of the functions
    something calls directly (default: dread.flat's callers). make=False: no search for a function
    without cases (quick_cases)."""
    import multiprocessing
    from dread import autocase, naming
    from dread.util.polite import die_with_parent, polite, resident, workers
    if called is None:
        from dread import flat
        called = set(flat.callers_of(program))
    gen = autocase.Generator(target, image, program=program)
    try:
        gen.endless()                       # (once, before the workers fork)
    except Exception:
        pass
    names = list(names)
    _G.clear()
    _G.update(program=program, gen=gen, flow=flow, named=naming.load(image, "globals"), called=set(called),
              make=make)
    try:
        n = min(len(names), jobs or workers(memory_each=resident()))
        if len(names) < POOL_MIN or n <= 1:
            texts = [_section_one(name) for name in names]
        else:
            polite()
            with multiprocessing.get_context("fork").Pool(n, initializer=die_with_parent) as pool:
                texts = pool.map(_section_one, names, chunksize=2)
    finally:
        _G.clear()
    return {name: t for name, t in zip(names, texts) if t is not None}


POOL_MIN = 8            # fewer functions than this are observed in this process
