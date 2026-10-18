"""Hand the slowest tasks of a pool out first.

A pool that takes its tasks in address order ends with a few slow functions on a few workers while
the others idle. longest_first() submits the tasks by the time each took in the previous run
(slowest first, tasks never seen before in front of those), yields the results in the order of the
items as given, so that nothing a caller prints or writes changes, and remembers the new times in a
JSON file (a run over fewer than `keep_from` items does not replace the times of a full run).
"""
import json
import os
import time


def _timed(fn, item):
    start = time.time()
    result = fn(item)
    return time.time() - start, result


def longest_first(pool, fn, items, key, times_file, keep_from=50):
    """fn and the items must be picklable; key(item) names an item across runs."""
    try:
        with open(times_file) as f:
            last = json.load(f)
    except (OSError, ValueError):
        last = {}
    items = list(items)
    order = sorted(range(len(items)), key=lambda i: -last.get(key(items[i]), 1e9))
    handles = {i: pool.apply_async(_timed, (fn, items[i])) for i in order}
    times = {}
    for i in range(len(items)):
        seconds, result = handles[i].get()
        times[key(items[i])] = round(seconds, 3)
        yield result
    if len(items) >= keep_from:
        os.makedirs(os.path.dirname(times_file) or ".", exist_ok=True)
        with open(times_file + ".tmp", "w") as f:
            json.dump(times, f)
        os.replace(times_file + ".tmp", times_file)
