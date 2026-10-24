"""dread gui's jobs: command lines run as processes, queued, logged and stoppable, their records kept.

A job is a command line (argv) with a working directory; dread gui's are DREAD's commands
(command(): `sys.executable -m dread.cli COMMAND ARGS...`). Jobs(DIRECTORY, limit) runs at most `limit`
of them at a time and queues the rest in the order they came. Each one:
- runs in a session of its own (its own process group), stdin from /dev/null, stdout and stderr
  together into DIRECTORY/N.log;
- runs polite, as dread.util.polite makes the tools: the lowest priority and, with $DREAD_FREE_CORES,
  off some cores; applied to the process right after it starts (its first milliseconds run at the
  server's priority, before Python has started; what it starts later inherits it), and $DREAD_POLITE=1
  tells the tools it is done; $DREAD_NICE=0 turns it off, as for the tools;
- gets the server's environment, with PYTHONUNBUFFERED=1 and what the caller adds (command() puts
  this DREAD in front of PYTHONPATH, so that a worktree's server runs its own code, not the editable
  install's);
- has a record, DIRECTORY/N.json (docs/formats.md, "dread gui's jobs"): the command line, the
  directory, who started it, when it was queued, started and ended, its state (STATES) and exit
  status. Never the environment: keys reach a job through it and nothing of it is kept or shown.

A job may wait for another (start(..., after=M)): it starts only once job M is done (exit status 0),
and is cancelled where M ended otherwise, so that a chain of jobs stops at the first that fails.

stop(N): SIGTERM to the job's process group (dread pilot cancels its batches on it), SIGKILL `grace`
seconds later (GRACE, or the job's own) if anything of the group is left; a queued job is cancelled.
close() (the server stops) stops every running job so and cancels the queued ones: the jobs live with
the server that started them.

The records survive the server. A new Jobs on the directory takes up what an old one left: a queued
job is cancelled; a job still marked running whose process is still alive (the same pid with the same
start time since the same boot, from Linux's /proc) is followed again (it can be stopped; its exit
status will be unknown, the process is not this server's child); one that is gone is marked lost (the
server ended without closing: killed, crashed). Elsewhere than on Linux a process cannot be
recognised, and such a job is marked lost without looking.

One Jobs per directory runs jobs: it holds a lock on DIRECTORY/lock. Another one on the same
directory (a second server on the same target), or one made read_only, only reads the records:
start() and stop() raise JobsError with the reason ("owner").

Log text is read in pieces from an offset (log()): whole lines (a line still being written waits for
its end, unless the job has ended), the values of the environment's secrets (variables named like
SECRET_NAMES, eight characters or more) and of the keys add_secret() was given replaced by their names
in case a command prints one; a
record's command line has them replaced too ("redacted": the words run are the real ones). Times are
the wall clock's (time.time()): the records say when things happened, they are not deterministic
output.
"""
import codecs
import fcntl
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time

GRACE = 30.0                # seconds from SIGTERM to SIGKILL when a job is stopped
TAIL = 128 * 1024           # bytes of a log the first look shows at most (its end)
CHUNK = 256 * 1024          # bytes one read of a log returns at most
POLL = 0.2                  # seconds between the watcher's looks at the processes
VERSION = 1                 # of the record's format
STATES = ("queued", "running", "stopping", "done", "failed", "stopped", "cancelled", "lost")
LIVE = ("queued", "running", "stopping")
SECRET_NAMES = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.I)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # (the directory dread is in)


class JobsError(Exception):
    pass


def command(name, args):
    """(argv, the command line as a person types it, what it adds to the environment) of one of
    DREAD's commands."""
    argv = [sys.executable, "-m", "dread.cli", name] + list(args)
    path = os.environ.get("PYTHONPATH")
    return argv, shlex.join(["dread", name] + list(args)), {"PYTHONPATH": ROOT + (os.pathsep + path if path else "")}


def identity(pid):
    """[start time in clock ticks since boot, boot id] of a running process (Linux's /proc), or None
    (also for a zombie: it has ended, whether or not its new parent has reaped it yet)."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            stat = f.read()
        fields = stat[stat.rindex(")") + 2:].split()
        if fields[0] in ("Z", "X"):
            return None
        start = int(fields[19])
        with open("/proc/sys/kernel/random/boot_id") as f:
            boot = f.read().strip()
        return [start, boot]
    except (OSError, ValueError, IndexError):
        return None


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def signal_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def be_polite(pid):
    """dread.util.polite for another process: its niceness raised to $DREAD_NICE_LEVEL (19), and with
    $DREAD_FREE_CORES its cores taken as polite() takes them. Returns the niceness it has, or None."""
    level = int(os.environ.get("DREAD_NICE_LEVEL", "19") or 19)
    nice = None
    try:
        now = os.getpriority(os.PRIO_PROCESS, pid)
        if level > now:
            os.setpriority(os.PRIO_PROCESS, pid, level)
        nice = max(level, now)
    except (OSError, AttributeError):
        pass
    try:
        free = int(os.environ.get("DREAD_FREE_CORES", "0") or 0)
        if free:
            cores = {}
            for cpu in sorted(os.sched_getaffinity(pid)):
                path = "/sys/devices/system/cpu/cpu%d/topology/core_id" % cpu
                core = open(path).read().strip() if os.path.exists(path) else str(cpu)
                cores.setdefault(core, []).append(cpu)
            ids = sorted(cores, key=lambda c: min(cores[c]))
            free = max(0, min(free, len(ids) // 2))
            if free:
                os.sched_setaffinity(pid, {cpu for c in ids[:len(ids) - free] for cpu in cores[c]})
    except (OSError, ValueError, AttributeError):
        pass
    return nice


def secrets_of(environ):
    """[(value, name)] of the environment's variables that look like secrets, longest values first."""
    found = [(v, k) for k, v in environ.items() if SECRET_NAMES.search(k) and len(v) >= 8]
    return sorted(found, key=lambda kv: -len(kv[0]))


def redact(text, secrets):
    for value, name in secrets:
        if value in text:
            text = text.replace(value, "[$%s]" % name)
    return text


class Jobs:
    """The jobs of one directory (see the module's docstring)."""

    def __init__(self, directory, limit=1, grace=GRACE, read_only=False):
        self.directory = directory
        self.limit = max(1, int(limit))
        self.grace = float(grace)
        self.cond = threading.Condition()
        self.records = {}           # number -> record, while this Jobs runs them (the owner)
        self.procs = {}             # number -> Popen of a job this Jobs started
        self.argv = {}              # number -> the words to run (the record's may be redacted)
        self.env = {}               # number -> what the caller adds to the environment (never kept)
        self.kill_at = {}           # process group -> when to SIGKILL what is left of it
        self.changed = 0            # counts the changes of records (the page reloads its list)
        self.closed = False
        self.thread = None
        self.lock_file = None
        self.secrets = secrets_of(os.environ)
        self.owner = None           # why this Jobs starts nothing, or None
        if read_only:
            self.owner = "this page is read-only (dread gui --read-only)"
            return
        os.makedirs(directory, exist_ok=True)
        if not self._lock():
            return
        with self.cond:
            self._take_up()
        self.thread = threading.Thread(target=self._watch, name="dread-gui-jobs", daemon=True)
        self.thread.start()

    # -- the directory --

    def _lock(self):
        f = open(os.path.join(self.directory, "lock"), "a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.seek(0)
            holder = f.read().strip()
            f.close()
            self.owner = "another dread gui runs the jobs of this target" + (" (process %s)" % holder if holder else "")
            return False
        f.seek(0)
        f.truncate()
        f.write("%d\n" % os.getpid())
        f.flush()
        self.lock_file = f
        return True

    def path(self, number, ext=".json"):
        return os.path.join(self.directory, "%d%s" % (number, ext))

    def _read(self, number):
        try:
            with open(self.path(number)) as f:
                record = json.load(f)
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) and record.get("id") == number else None

    def _numbers(self):
        try:
            names = os.listdir(self.directory)
        except OSError:
            return []
        return sorted(int(n[:-5]) for n in names if n.endswith(".json") and n[:-5].isdigit())

    def _save(self, record):
        """Write a record (whole, then renamed into place). Called with the condition held."""
        path = self.path(record["id"])
        with open(path + ".tmp", "w") as f:
            json.dump(record, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(path + ".tmp", path)
        self.changed += 1
        self.cond.notify_all()

    def _take_up(self):
        """The records a server before this one left: queued ones cancelled, running ones followed
        while their process lives, else lost. Called with the condition held."""
        for number in self._numbers():
            record = self._read(number)
            if record is None:
                continue
            self.records[number] = record
            state = record.get("state")
            if state == "queued":
                self._end(record, "cancelled", None, "the server stopped before it started")
            elif state in ("running", "stopping"):
                pid, known = record.get("pid"), record.get("proc")
                if pid and known and identity(pid) == known:
                    record["adopted"] = True
                    record["why"] = "it outlived the server that started it and is followed again (its exit status " \
                                    "will be unknown)"
                    if state == "stopping":
                        self.kill_at[pid] = time.time() + float(record.get("grace") or self.grace)
                    self._save(record)
                else:
                    self._end(record, "lost", None, "the server ended while it ran (killed or crashed); how the job "
                                                    "ended is unknown")

    # -- the watcher --

    def _watch(self):
        with self.cond:
            while not self.closed:
                try:
                    self._step()
                except Exception as e:          # (keep watching: say what went wrong and go on)
                    print("dread gui: jobs: %s" % e, file=sys.stderr)
                self.cond.wait(POLL)

    def _step(self):
        """One look: ended processes, stops past their grace, queued jobs started where there is room.
        Called with the condition held."""
        now = time.time()
        for number, proc in list(self.procs.items()):
            code = proc.poll()
            if code is not None:
                del self.procs[number]
                record = self.records[number]
                if record["state"] == "stopping":
                    self._end(record, "stopped", code, record.get("why"))
                else:
                    self._end(record, "done" if code == 0 else "failed", code, None)
        for record in list(self.records.values()):
            if record.get("adopted") and record["state"] in ("running", "stopping") and \
                    identity(record["pid"]) != record.get("proc"):
                if record["state"] == "stopping":
                    self._end(record, "stopped", None, record.get("why"))
                else:
                    self._end(record, "lost", None, "it ended while no server watched it; its exit status is unknown")
        for pgid, when in list(self.kill_at.items()):
            if not group_alive(pgid):
                del self.kill_at[pgid]
            elif now >= when:
                signal_group(pgid, signal.SIGKILL)
                del self.kill_at[pgid]
        busy = sum(1 for r in self.records.values() if r["state"] in ("running", "stopping"))
        for number in sorted(n for n, r in self.records.items() if r["state"] == "queued"):
            first = self.records.get(self.records[number].get("after"))
            if first is not None and first["state"] != "done":
                if first["state"] not in LIVE:      # (a chain stops at the first job that does not succeed)
                    self.env.pop(number, None)
                    self.argv.pop(number, None)
                    self._end(self.records[number], "cancelled", None,
                              "job %d, which it waited for, ended %s" % (first["id"], first["state"]))
                continue
            if busy >= self.limit:
                break
            self._launch(self.records[number])
            busy += 1

    def _launch(self, record):
        number = record["id"]
        env = dict(os.environ)
        env.update(self.env.pop(number, {}))
        env["PYTHONUNBUFFERED"] = "1"
        polite = record.get("polite", True) and os.environ.get("DREAD_NICE", "1") != "0"
        if polite:
            env["DREAD_POLITE"] = "1"
        argv = self.argv.pop(number, record["argv"])
        now = time.time()
        with open(self.path(number, ".log"), "ab", buffering=0) as log:
            try:
                proc = subprocess.Popen(argv, cwd=record["cwd"], env=env, stdin=subprocess.DEVNULL, stdout=log,
                                        stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as e:
                log.write(("dread gui: the job did not start: %s\n" % e).encode())
                record["started"] = now
                self._end(record, "failed", None, "it did not start: %s" % e)
                return
        record.update(state="running", started=now, pid=proc.pid, proc=identity(proc.pid),
                      nice=be_polite(proc.pid) if polite else None)
        self.procs[number] = proc
        self._save(record)

    def _end(self, record, state, code, why):
        record.update(state=state, exit=code, ended=time.time(), why=why)
        self._save(record)

    # -- what the server asks --

    def start(self, argv, cwd, line=None, command=None, by="dread gui", env=None, grace=None, polite=True,
              after=None):
        """Queue a job; it starts at once where fewer than `limit` run (and job `after`, if given, is
        done). Returns its record."""
        with self.cond:
            if self.owner:
                raise JobsError(self.owner)
            if self.closed:
                raise JobsError("the server is stopping")
            if not os.path.isdir(cwd):
                raise JobsError("no directory %s" % cwd)
            if after is not None and after not in self.records:
                raise JobsError("no job %s to wait for" % after)
            number = max(self.records, default=0) + 1
            shown = line or shlex.join(argv)
            kept = [redact(a, self.secrets) for a in argv]
            record = {"version": VERSION, "id": number, "command": command, "argv": kept,
                      "line": redact(shown, self.secrets), "cwd": cwd, "by": by, "state": "queued",
                      "queued": time.time(), "started": None, "ended": None, "exit": None, "pid": None, "proc": None,
                      "nice": None, "polite": bool(polite), "grace": float(grace) if grace else self.grace, "why": None,
                      "log": "%d.log" % number, "after": after}
            if kept != list(argv) or record["line"] != shown:
                record["redacted"] = True
            self.argv[number] = list(argv)
            self.env[number] = dict(env or {})
            open(self.path(number, ".log"), "wb").close()
            self.records[number] = record
            self._save(record)
            self._step()
            return dict(record)

    def add_secret(self, value, name):
        """A value replaced by [$name] in the logs served and the command lines kept from now on, beside the
        environment's secrets (a key file's key: dread gui reads the providers' at start, and a key written
        from its Setup page)."""
        if value and len(value) >= 8:
            with self.cond:
                self.secrets = sorted(set(self.secrets) | {(value, name)}, key=lambda kv: (-len(kv[0]), kv[1]))

    def stop(self, number, why="stopped from the page"):
        """Stop a job: a queued one is cancelled, a running one gets SIGTERM, SIGKILL after its grace."""
        with self.cond:
            if self.owner:
                raise JobsError(self.owner)
            record = self.records.get(number)
            if record is None:
                raise KeyError(number)
            if record["state"] == "queued":
                self.env.pop(number, None)
                self.argv.pop(number, None)
                self._end(record, "cancelled", None, why)
            elif record["state"] == "running":
                record.update(state="stopping", why=why, stopping=time.time())
                signal_group(record["pid"], signal.SIGTERM)
                self.kill_at[record["pid"]] = time.time() + float(record.get("grace") or self.grace)
                self._save(record)
            return dict(record)

    def list(self, limit=None):
        """The records, newest first."""
        if self.owner:
            records = [r for r in (self._read(n) for n in reversed(self._numbers())) if r]
        else:
            with self.cond:
                records = [dict(self.records[n]) for n in sorted(self.records, reverse=True)]
        return records[:limit] if limit else records

    def get(self, number):
        if self.owner:
            record = self._read(number)
        else:
            with self.cond:
                record = dict(self.records[number]) if number in self.records else None
        if record is None:
            raise KeyError(number)
        return record

    def status(self):
        """{"running", "queued", "limit", "changed", "owner"}: what the page's header shows; "changed"
        changes whenever a record does."""
        records = self.list()
        changed = self.changed
        if self.owner:
            changed = max((os.path.getmtime(self.path(r["id"])) for r in records), default=0)
        return {"running": sum(r["state"] in ("running", "stopping") for r in records),
                "queued": sum(r["state"] == "queued" for r in records), "limit": self.limit, "changed": changed,
                "owner": self.owner}

    def log(self, number, offset=None, limit=CHUNK):
        """A piece of a job's log: {"id", "start", "next", "size", "text", "state", "more"} from byte
        `offset` (None: the last TAIL bytes, from a line's start); whole lines only, unless the job has
        ended and this is the end of its log (or a single line is longer than `limit`)."""
        record = self.get(number)
        live = record["state"] in LIVE
        path = self.path(number, ".log")
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        tail = offset is None or offset < 0
        start = max(0, size - TAIL) if tail else min(int(offset), size)
        data = b""
        if size > start:
            with open(path, "rb") as f:
                if tail and start:                  # (the tail from the start of a line)
                    f.seek(start - 1)
                    nl = f.read(min(size - start + 1, 64 * 1024)).find(b"\n")
                    start += max(nl, 0)
                f.seek(start)
                data = f.read(min(limit, size - start))
        end = len(data)
        complete = not live and start + end >= size
        if not complete:
            last = max(data.rfind(b"\n"), data.rfind(b"\r"))
            if last >= 0:
                end = last + 1
            elif len(data) < limit:
                end = 0
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        text = decoder.decode(data[:end], final=complete)
        end -= len(decoder.getstate()[0])
        return {"id": number, "start": start, "next": start + end, "size": size, "text": redact(text, self.secrets),
                "state": record["state"], "more": start + end < size}

    def close(self, timeout=None):
        """Stop the running jobs (SIGTERM, SIGKILL after their grace or `timeout`), cancel the queued
        ones, end the watcher and give up the directory. Returns whether every job's processes are gone."""
        with self.cond:
            if self.closed:
                return True
            self.closed = True
            self.cond.notify_all()
        if self.thread is not None:
            self.thread.join()
        if self.owner:
            return True
        with self.cond:
            deadline = time.time()
            for record in self.records.values():
                if record["state"] == "queued":
                    self._end(record, "cancelled", None, "the server stopped before it started")
                elif record["state"] in ("running", "stopping"):
                    if record["state"] == "running":
                        record.update(state="stopping", why="the server stopped", stopping=time.time())
                        self._save(record)
                    signal_group(record["pid"], signal.SIGTERM)
                    grace = float(record.get("grace") or self.grace)
                    deadline = max(deadline, time.time() + (grace if timeout is None else min(grace, timeout)))
            while time.time() < deadline and any(r["state"] == "stopping" for r in self.records.values()):
                self._step()
                self.cond.wait(0.05)
            for record in self.records.values():
                if record["state"] == "stopping":
                    signal_group(record["pid"], signal.SIGKILL)
            until = time.time() + 5
            while time.time() < until and any(r["state"] == "stopping" for r in self.records.values()):
                self._step()
                self.cond.wait(0.05)
            gone = not any(r["state"] in LIVE for r in self.records.values())
            for pgid in list(self.kill_at):         # (what a stop before left of a group: the server goes)
                signal_group(pgid, signal.SIGKILL)
            self.kill_at = {}
        if self.lock_file is not None:
            self.lock_file.close()
            self.lock_file = None
        return gone
