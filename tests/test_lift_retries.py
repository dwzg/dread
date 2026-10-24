"""dread.lift Retries: the lift check's second chances for functions that failed while a lifted
function in their call tree failed too, bottom up, each function once, without a fixed number of
passes (8 until 2026-10-24: a private firmware's deep call trees needed more)."""
from dread.lift import Retries


def chain(n):
    """f0 calls f1 calls ... f(n-1): {name: the names below it}."""
    names = ["f%d" % i for i in range(n)]
    return names, {name: set(names[i + 1:]) for i, name in enumerate(names)}


def run(retries, refail=()):
    """Every pass of a check whose retried functions pass, but those in refail, which fail again (with
    the failure below them still there): the passes' sets in order."""
    order = []
    while True:
        ready = retries.next()
        if not ready:
            return order
        order.append(sorted(ready))
        again = set(ready) & set(refail)
        if again:                           # they fail again, with a failure below them once more
            retries.failed(again | {"f11"})


def test_suspects_are_tried_bottom_up_one_layer_a_pass_beyond_eight_passes():
    names, tree = chain(12)
    r = Retries(tree)
    r.failed(names)                         # all fail in one round; f11 (nothing below it) is the culprit
    assert r.suspects == set(names[:-1])
    assert run(r) == [[n] for n in reversed(names[:-1])]    # eleven passes, the deepest first
    assert r.passes == 11 and not r.suspects


def test_a_function_tried_again_is_not_a_suspect_again():
    """One that fails again on its retry, with a failure below it once more, stays out: each function
    is tried again once, so the passes end by themselves."""
    names, tree = chain(12)
    r = Retries(tree)
    r.failed(names)
    order = run(r, refail=names[:-1])
    assert order == [[n] for n in reversed(names[:-1])] and r.passes == 11 and not r.suspects


def test_final_failures_and_lone_failures_are_no_suspects_and_a_cycle_goes_at_once():
    names, tree = chain(3)
    r = Retries(tree)
    r.failed(["f0", "f2"], final=["f0"])    # f0: no cases from the original (final)
    assert r.suspects == set()
    r.failed(["f1"])                        # alone: nothing below it failed
    assert r.suspects == set()
    cycle = Retries({"a": {"b"}, "b": {"a"}, "c": set()})
    cycle.failed(["a", "b", "c"])
    assert cycle.suspects == {"a", "b"} and cycle.next() == {"a", "b"} and cycle.next() == set()


def test_the_bound_stops_the_passes_and_leaves_the_rest_to_report():
    names, tree = chain(12)
    r = Retries(tree, bound=3)
    r.failed(names)
    assert run(r) == [["f10"], ["f9"], ["f8"]]
    assert r.suspects == set(names[:8])     # (the check prints them as "untried")
