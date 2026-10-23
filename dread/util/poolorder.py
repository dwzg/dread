"""Hand the slowest tasks of a pool out first.

A pool that takes its tasks in address order ends with a few slow functions on a few workers while
the others idle. longest_first() submits the tasks by the time each took in the previous run
(slowest first, tasks never seen before in front of those), yields the results in the order of the
items as given, so that nothing a caller prints or writes changes, and remembers the new times in a
JSON file (a run over fewer than `keep_from` items does not replace the times of a full run).

DREAD_ITEMS_LOG=FILE: each worker appends a line to FILE when it starts an item and when it ends
one, with its pid and its memory (tab-separated: `start`, item, pid, time, resident MB; `end`,
item, pid, time, seconds, resident MB, the process's peak MB so far). For a bulk run whose memory
grows: the pid that grows, found with ps, names the item it is on, which an item that never ends
(a run killed for its memory) would not tell otherwise. Lines are short single writes to a file
opened for appending, so the workers' lines do not mix. With it, a worker also writes the Python
stack of each of its threads to its stderr when it gets SIGUSR1 (`kill -USR1 PID`; faulthandler):
what an item that grows or hangs is doing, without a debugger (which ptrace's scope may forbid).
"""
import json
import os
import time

ITEMS_LOG = "DREAD_ITEMS_LOG"


def _resident_mb():
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") >> 20
    except (OSError, ValueError, IndexError):
        return -1


def _peak_mb():
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss >> 10     # (KB on Linux)
    except (ImportError, OSError):
        return -1


def _log_item(path, fields):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, ("\t".join(str(x) for x in fields) + "\n").encode())
    finally:
        os.close(fd)


_dumps = []


def _dump_on_signal():
    """Once per process: SIGUSR1 writes every thread's stack to stderr (faulthandler)."""
    if _dumps:
        return
    _dumps.append(True)
    try:
        import faulthandler
        import signal
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except (ImportError, AttributeError, ValueError, OSError, RuntimeError):
        pass


def _timed(fn, item, name=None):
    log = os.environ.get(ITEMS_LOG) if name is not None else None
    if log:
        _dump_on_signal()
        _log_item(log, ("start", name, os.getpid(), "%.1f" % time.time(), _resident_mb()))
    start = time.time()
    result = fn(item)
    seconds = time.time() - start
    if log:
        _log_item(log, ("end", name, os.getpid(), "%.1f" % time.time(), "%.3f" % seconds, _resident_mb(),
                        _peak_mb()))
    return seconds, result


def longest_first(pool, fn, items, key, times_file, keep_from=50):
    """fn and the items must be picklable; key(item) names an item across runs."""
    try:
        with open(times_file) as f:
            last = json.load(f)
    except (OSError, ValueError):
        last = {}
    items = list(items)
    order = sorted(range(len(items)), key=lambda i: -last.get(key(items[i]), 1e9))
    started = time.time()
    handles = {i: pool.apply_async(_timed, (fn, items[i], str(key(items[i])))) for i in order}
    times = {}
    for i in range(len(items)):
        seconds, result = handles[i].get()
        times[key(items[i])] = round(seconds, 3)
        yield result
    report(times_file, times, time.time() - started)
    if len(items) >= keep_from:
        os.makedirs(os.path.dirname(times_file) or ".", exist_ok=True)
        with open(times_file + ".tmp", "w") as f:
            json.dump(times, f)
        os.replace(times_file + ".tmp", times_file)


def report(times_file, times, wall):
    """TIMES.tsv: what each item took, slowest first, and the total against the wall clock."""
    path = os.path.splitext(times_file)[0] + ".tsv"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".tmp", "w") as f:
        f.write("item\tseconds\n")
        for name, seconds in sorted(times.items(), key=lambda kv: (-kv[1], kv[0])):
            f.write("%s\t%.3f\n" % (name, seconds))
        f.write("# total %.1f s of work in %.1f s\n" % (sum(times.values()), wall))
    os.replace(path + ".tmp", path)
