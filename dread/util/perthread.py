"""Caches of Unicorn engines, one for each thread.

A Unicorn engine is not for two threads at once: on 2026-10-23 `dread pilot --task review`, asking
eight questions at a time, showed listings of functions without readable C, whose constant arguments
dread.constants finds by running code in an engine it kept for the program; two threads ran in it at
once and Unicorn crashed in its code generator (tcg_gen_code_arm, a segmentation fault of the whole
process). The same held for dread.switches' engines and dread.emu's cores. Each thread now keeps its
own (threading.local): the engines a thread made end with it; a forked worker starts with copies of
the forking thread's, as before."""
import threading


class PerThread:
    """A dict of one's own in each thread: `PerThread()` as a module's cache (`.get()` gives this
    thread's dict), or as a class attribute, which reads as this thread's dict (Core.cores)."""

    def __init__(self):
        self._local = threading.local()

    def get(self):
        d = getattr(self._local, "d", None)
        if d is None:
            d = self._local.d = {}
        return d

    def __get__(self, obj, owner):
        return self.get()
