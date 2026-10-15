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

Background work yields (verdict 6, punch 3; lane YIELD): a scope made with
`background=True` (the previews, posters and range proxies the media walk makes, the
index's proxies and close looks) is one whose ffmpeg the server may pause while the
Program plays. Its ffmpeg children start at nice 19 and every process it starts carries
the thread's `tagged(key)` (what it builds), so a build somebody is waiting on can be
left running. `background_procs()` lists them, descendants included.

Orphans (RANGEPROXY's request): an ffmpeg started in any scope gets PR_SET_PDEATHSIG
(SIGKILL), via `setpriv --pdeathsig` in front of it, so it dies with the server even when
the server is SIGKILLed; `kill_all()` ends every scope's groups on a clean shutdown.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
import weakref
from contextlib import contextmanager
from typing import Iterator

_LOCAL = threading.local()
_ORIGINAL = subprocess.Popen
GRACE_S = 1.5
BACKGROUND_NICE = 19
_SCOPES: "weakref.WeakSet[Scope]" = weakref.WeakSet()
_SCOPES_LOCK = threading.Lock()


class Scope:
    """One job's child processes. `background`: work that yields to playback."""

    def __init__(self, name: str = "", background: bool = False) -> None:
        self.name = name
        self.background = background
        self.cancelled = False
        self._procs: list[subprocess.Popen] = []
        self._lock = threading.Lock()
        with _SCOPES_LOCK:
            _SCOPES.add(self)

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
        if sig == signal.SIGTERM:
            # a group paused for playback (SIGSTOP) holds a SIGTERM until it runs again
            os.killpg(proc.pid, signal.SIGCONT)
    except (ProcessLookupError, PermissionError):
        try:
            proc.send_signal(sig)
        except (ProcessLookupError, OSError):
            pass


def scopes() -> list[Scope]:
    with _SCOPES_LOCK:
        return list(_SCOPES)


def kill_all(grace_s: float = GRACE_S) -> int:
    """Kill every scope's live process groups (the server is going away)."""
    live = [sc for sc in scopes() if sc.alive()]
    done: list[int] = []
    threads = [threading.Thread(target=lambda sc=sc: done.append(sc.kill(grace_s)),
                                daemon=True) for sc in live]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=grace_s + 1.0)
    return sum(done)


def current() -> Scope | None:
    return getattr(_LOCAL, "scope", None)


def current_tag() -> str | None:
    return getattr(_LOCAL, "tag", None)


@contextmanager
def tagged(tag: str | None) -> Iterator[None]:
    """Every process this thread starts in the block carries `tag` (what it builds)."""
    prev = current_tag()
    _LOCAL.tag = tag
    try:
        yield
    finally:
        _LOCAL.tag = prev


@contextmanager
def active(scope: Scope | None) -> Iterator[Scope | None]:
    """Run the block with `scope` as this thread's: every process it starts joins it."""
    prev = current()
    _LOCAL.scope = scope
    try:
        yield scope
    finally:
        _LOCAL.scope = prev


_SETPRIV: list = []          # [path or None], looked up once
_NICE: list = []


def _which(cache: list, name: str) -> str | None:
    if not cache:
        cache.append(shutil.which(name))
    return cache[0]


def is_ffmpeg(args) -> bool:
    if isinstance(args, (list, tuple)) and args:
        return os.path.basename(str(args[0])) == "ffmpeg"
    return False


def _wrap(args, scope: Scope):
    """An ffmpeg in a scope dies with the server (PR_SET_PDEATHSIG via setpriv; the
    parent is the thread that waits on it, which outlives it) and, in a background
    scope, starts at nice 19 (set before exec, so every ffmpeg thread inherits it)."""
    if os.name != "posix" or not is_ffmpeg(args):
        return args
    out = list(args)
    setpriv = _which(_SETPRIV, "setpriv")
    if setpriv:
        out = [setpriv, "--pdeathsig", "KILL", "--", *out]
    nice = _which(_NICE, "nice") if scope.background else None
    if nice:
        out = [nice, "-n", str(BACKGROUND_NICE), *out]
    return out


class ScopedPopen(_ORIGINAL):  # type: ignore[misc, valid-type]
    def __init__(self, args, *a, **kw):
        scope = current()
        self.rc_tag = current_tag()
        self.rc_ffmpeg = is_ffmpeg(args)
        if scope is not None:
            kw.setdefault("start_new_session", True)
            if not kw.get("shell"):
                args = _wrap(args, scope)
        super().__init__(args, *a, **kw)
        if scope is not None:
            scope.add(self)


def install() -> None:
    """Make `subprocess.Popen` (and so `subprocess.run`) scope-aware. Idempotent."""
    if subprocess.Popen is not ScopedPopen:
        subprocess.Popen = ScopedPopen  # type: ignore[misc]


def _kids_map() -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return kids
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
    return kids


def children_of(pids: list[int], kids: dict[int, list[int]] | None = None) -> list[int]:
    """Every live descendant of `pids` (from /proc), for tests and diagnostics."""
    if kids is None:
        kids = _kids_map()
    out, todo = [], list(pids)
    while todo:
        p = todo.pop()
        for c in kids.get(p, []):
            out.append(c)
            todo.append(c)
    return out

def comm(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def background_procs() -> list[tuple[int, str | None]]:
    """Every live ffmpeg of a background scope, as (pid, tag): its own children and
    their descendants (a close look's tool runs its own ffmpeg)."""
    roots: list[tuple[int, str | None]] = []
    for sc in scopes():
        if sc.background and not sc.cancelled:
            roots += [(p.pid, getattr(p, "rc_tag", None)) for p in sc.alive()]
    if not roots:
        return []
    out: list[tuple[int, str | None]] = []
    kids = _kids_map()
    for pid, tag in roots:
        for p in [pid, *children_of([pid], kids)]:
            if comm(p) == "ffmpeg":
                out.append((p, tag))
    return out
