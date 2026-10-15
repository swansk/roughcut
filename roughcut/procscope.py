"""Stop means stop: every process a job starts, killable as one (verdict 3, punch 5).

Verdict 3: the first pass's Stop took 5 min 25 s, because the index only looked at its
cancel flag *between* clip-stages and a stage is a 5-minute ffmpeg encode or a contact
sheet read by `claude -p`. The export's cancel was already instant — it runs its tool in
its own process group and kills the group (`server._run_render`). This gives every other
job the same property without rewriting each place that shells out:

  * a `Scope` is a job's set of child processes;
  * a thread that enters a scope (`with procscope.active(scope):`) has every
    `subprocess.Popen` it creates — `subprocess.run` included, and so the index's tools,
    the proxy encode and the CLI backend's `claude -p` — started in a new session (its
    own process group) and registered with the scope;
  * `scope.kill()` sends SIGTERM to every registered group, then SIGKILL to whatever is
    left after a short grace, and marks the scope cancelled: a process the thread starts
    after that is killed the moment it exists, so a stage that was about to start the
    next tool cannot outrun the Stop.

Threads outside a scope are untouched: `install()` swaps `subprocess.Popen` for a
subclass that behaves exactly like the original unless the creating thread has an
active scope.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from typing import Iterator

_LOCAL = threading.local()
_ORIGINAL = subprocess.Popen
GRACE_S = 1.5


class Scope:
    """One job's child processes."""

    def __init__(self, name: str = "") -> None:
        self.name = name
        self.cancelled = False
        self._procs: list[subprocess.Popen] = []
        self._lock = threading.Lock()

    def add(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.append(proc)
            dead = self.cancelled
        if dead:
            _signal_group(proc, signal.SIGKILL)

    def alive(self) -> list[subprocess.Popen]:
        with self._lock:
            self._procs = [p for p in self._procs if p.poll() is None]
            return list(self._procs)

    def pids(self) -> list[int]:
        return [p.pid for p in self.alive()]

    def kill(self, grace_s: float = GRACE_S) -> int:
        """SIGTERM every live group, SIGKILL what is left after `grace_s`. Returns how
        many processes were signalled. Safe to call twice and from any thread."""
        with self._lock:
            self.cancelled = True
            procs = list(self._procs)
        live = [p for p in procs if p.poll() is None]
        for p in live:
            _signal_group(p, signal.SIGTERM)
        deadline = time.time() + grace_s
        while time.time() < deadline and any(p.poll() is None for p in live):
            time.sleep(0.05)
        for p in live:
            if p.poll() is None:
                _signal_group(p, signal.SIGKILL)
        return len(live)


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)          # its own session: the pid is the group id
    except (ProcessLookupError, PermissionError):
        try:
            proc.send_signal(sig)
        except (ProcessLookupError, OSError):
            pass


def current() -> Scope | None:
    return getattr(_LOCAL, "scope", None)


@contextmanager
def active(scope: Scope | None) -> Iterator[Scope | None]:
    """Run the block with `scope` as this thread's: every process it starts joins it."""
    prev = current()
    _LOCAL.scope = scope
    try:
        yield scope
    finally:
        _LOCAL.scope = prev


class ScopedPopen(_ORIGINAL):  # type: ignore[misc, valid-type]
    def __init__(self, args, *a, **kw):
        scope = current()
        if scope is not None:
            kw.setdefault("start_new_session", True)
        super().__init__(args, *a, **kw)
        if scope is not None:
            scope.add(self)


def install() -> None:
    """Make `subprocess.Popen` (and so `subprocess.run`) scope-aware. Idempotent."""
    if subprocess.Popen is not ScopedPopen:
        subprocess.Popen = ScopedPopen  # type: ignore[misc]


def children_of(pids: list[int]) -> list[int]:
    """Every live descendant of `pids` (from /proc), for tests and diagnostics."""
    kids: dict[int, list[int]] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return []
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", encoding="utf-8") as fh:
                stat = fh.read()
        except OSError:
            continue
        fields = stat.rsplit(")", 1)[-1].split()
        if len(fields) < 2 or fields[0] == "Z":
            continue
        kids.setdefault(int(fields[1]), []).append(int(name))
    out, todo = [], list(pids)
    while todo:
        p = todo.pop()
        for c in kids.get(p, []):
            out.append(c)
            todo.append(c)
    return out
