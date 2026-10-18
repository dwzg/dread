"""Keep a bulk run from slowing down what else the machine does.

polite() lowers the priority of the calling process to the minimum and takes it (and the workers
it starts later) off a few physical cores. Priority alone is not enough: with every hardware thread
busy, a program that needs one fast thread still shares its core with a busy sibling, and the whole
processor runs at its all-core clock. On the machine this was measured on (16 cores, a local
language model driving a GPU from one CPU thread), the other program got back most of its speed
with two cores left free.

A process started by one that is already polite inherits its priority and cores and does nothing
more ($DREAD_POLITE=1 marks that), so nested tools do not take two more cores each.

Environment:
  DREAD_NICE=0          turn both off
  DREAD_NICE_LEVEL=n    niceness n instead of 19 (only upwards)
  DREAD_FREE_CORES=n    physical cores left free (default 0; at most half of them)
  DREAD_JOBS=n          cap on every pool's workers (workers())

Linux only; elsewhere the priority is lowered where possible and the rest is skipped.
"""
import os


def polite():
    if os.environ.get("DREAD_NICE", "1") == "0" or os.environ.get("DREAD_POLITE") == "1":
        return
    os.environ["DREAD_POLITE"] = "1"
    level = int(os.environ.get("DREAD_NICE_LEVEL", "19") or 19)
    try:
        os.nice(max(0, level - os.nice(0)))
    except (OSError, AttributeError):
        pass
    try:
        free = int(os.environ.get("DREAD_FREE_CORES", "0") or 0)
        if not free:
            return
        cores = {}
        for cpu in sorted(os.sched_getaffinity(0)):
            path = "/sys/devices/system/cpu/cpu%d/topology/core_id" % cpu
            core = open(path).read().strip() if os.path.exists(path) else str(cpu)
            cores.setdefault(core, []).append(cpu)
        ids = sorted(cores, key=lambda c: min(cores[c]))
        free = max(0, min(free, len(ids) // 2))
        if free:
            os.sched_setaffinity(0, {cpu for c in ids[:len(ids) - free] for cpu in cores[c]})
    except (OSError, ValueError, AttributeError):
        pass


def workers():
    """How many workers a pool should have: the hardware threads this process may use (after
    polite(), those of the cores not left free), capped by $DREAD_JOBS."""
    try:
        n = len(os.sched_getaffinity(0)) or 1
    except AttributeError:
        n = os.cpu_count() or 1
    cap = int(os.environ.get("DREAD_JOBS", 0) or 0)
    return min(n, cap) if cap > 0 else n


def die_with_parent():
    """Ask the kernel to kill this process when its parent dies (Linux; elsewhere nothing). For
    pool workers and for the runs a mutation check starts: a worker whose parent was killed is
    otherwise reparented and runs on."""
    try:
        import ctypes
        import signal
        parent = os.getppid()
        ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL)      # PR_SET_PDEATHSIG
        if os.getppid() != parent:
            os._exit(1)
    except (OSError, AttributeError):
        pass
