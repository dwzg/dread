"""dread.util.poolorder: the slowest first, results in the items' order, and the items log."""
import multiprocessing
import os

from dread.util import poolorder


def _square(x):
    return x * x


def test_results_keep_the_items_order_and_the_items_log_names_each_item(tmp_path, monkeypatch):
    log = tmp_path / "items.log"
    monkeypatch.setenv(poolorder.ITEMS_LOG, str(log))
    times = tmp_path / "times" / "squares.json"
    with multiprocessing.get_context("fork").Pool(2) as pool:
        got = list(poolorder.longest_first(pool, _square, [3, 1, 2], str, str(times), keep_from=1))
    assert got == [9, 1, 4]
    lines = [line.split("\t") for line in log.read_text().splitlines()]
    starts = sorted(f[1] for f in lines if f[0] == "start")
    ends = {f[1]: f for f in lines if f[0] == "end"}
    assert starts == ["1", "2", "3"] and sorted(ends) == ["1", "2", "3"]
    for f in ends.values():
        assert len(f) == 7
        assert int(f[2]) != os.getpid()             # written by the worker
        assert float(f[4]) >= 0 and int(f[5]) > 0 and int(f[6]) >= int(f[5]) - 1
    assert (tmp_path / "times" / "squares.tsv").exists()


def test_no_items_log_without_the_variable(tmp_path, monkeypatch):
    monkeypatch.delenv(poolorder.ITEMS_LOG, raising=False)
    with multiprocessing.get_context("fork").Pool(1) as pool:
        got = list(poolorder.longest_first(pool, _square, [2], str, str(tmp_path / "t.json")))
    assert got == [4]
    assert not list(tmp_path.glob("*.log"))


def test_with_the_items_log_a_worker_writes_its_stack_on_sigusr1(tmp_path):
    import subprocess
    import sys
    code = (
        "import os, signal\n"
        "from dread.util import poolorder\n"
        "def snooze_marker_fn(x):\n"
        "    os.kill(os.getpid(), signal.SIGUSR1)\n"
        "    return x\n"
        "print(poolorder._timed(snooze_marker_fn, 7, 'seven')[1])\n")
    env = dict(os.environ, DREAD_ITEMS_LOG=str(tmp_path / "items.log"),
               PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    got = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "7"
    assert "snooze_marker_fn" in got.stderr
