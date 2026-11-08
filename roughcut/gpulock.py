"""One GPU job at a time on the editor's machine.

The local understanding stage (`localgpu/`, wave 20) and the audio pass both want the one
12 GB card. A lock file serialises them across processes: `fcntl.flock` on
`~/.cache/roughcut/gpu.lock` (or `$ROUGHCUT_GPU_LOCK`). The kernel drops the lock if the
holder dies, so a crash never leaves it stuck.

Stdlib only: the heavy `localgpu` environment imports this file too.

    with gpulock.hold("local"):          # blocks until the card is free
        ...
    gpulock.waiting()                    # who else is queued behind the holder (names)

The holder writes who it is into the lock file (for the status line); waiters leave a
marker file so a long job can step aside between its units of work (`yield_to_waiters`)
instead of keeping a short job — the audio pass the first pass waits on — behind it for
minutes.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from pathlib import Path


def lock_path() -> Path:
    env = os.environ.get("ROUGHCUT_GPU_LOCK")
    return Path(env) if env else Path.home() / ".cache" / "roughcut" / "gpu.lock"


def _want_dir() -> Path:
    p = lock_path()
    return p.with_name(p.name + ".want")


def holder() -> dict | None:
    """Who holds the card now ({"owner", "pid", "since"}), or None. Read-only; may be
    stale by the time the caller looks."""
    try:
        data = json.loads(lock_path().read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return None
    pid = data.get("pid")
    if not pid:
        return None
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return None
    # the file keeps the last holder's note after release; only a held lock counts
    try:
        fd = os.open(lock_path(), os.O_RDWR)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return data
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


def waiting() -> list[str]:
    """Owners queued for the card right now (markers whose process is alive)."""
    out = []
    d = _want_dir()
    try:
        names = list(d.iterdir())
    except OSError:
        return out
    for m in names:
        try:
            pid = int(m.name.split("-", 1)[0])
            os.kill(pid, 0)
        except (OSError, ValueError):
            with contextlib.suppress(OSError):
                m.unlink()
            continue
        out.append(m.name.split("-", 1)[1] if "-" in m.name else m.name)
    return out


class Lock:
    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.fd: int | None = None

    def acquire(self, timeout: float | None = None, poll: float = 0.25, abort=None) -> bool:
        """Take the card, waiting up to `timeout` s (None: as long as it takes). `abort()` is
        asked every poll; true stops the wait (a Stop pressed while queued) and returns False."""
        p = lock_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o644)
        marker = _want_dir() / f"{os.getpid()}-{self.owner}"
        t0 = time.monotonic()
        marked = False
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if (timeout is not None and time.monotonic() - t0 >= timeout) \
                            or (abort is not None and abort()):
                        os.close(fd)
                        return False
                    if not marked:
                        _want_dir().mkdir(parents=True, exist_ok=True)
                        marker.write_text("", encoding="utf-8")
                        marked = True
                    time.sleep(poll)
        finally:
            if marked:
                with contextlib.suppress(OSError):
                    marker.unlink()
        os.ftruncate(fd, 0)
        os.pwrite(fd, json.dumps({"owner": self.owner, "pid": os.getpid(),
                                  "since": time.time()}).encode(), 0)
        self.fd = fd
        return True

    def release(self) -> None:
        if self.fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(self.fd)
            self.fd = None

    def yield_to_waiters(self, pause: float = 1.0) -> bool:
        """Step aside if somebody else is queued: release, let them take the card, then
        queue again. True if it stepped aside."""
        if self.fd is None or not waiting():
            return False
        self.release()
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline and waiting():
            time.sleep(0.25)
        time.sleep(pause)
        self.acquire()
        return True


@contextlib.contextmanager
def hold(owner: str, timeout: float | None = None, abort=None):
    lk = Lock(owner)
    if not lk.acquire(timeout, abort=abort):
        raise TimeoutError(f"the GPU is busy ({(holder() or {}).get('owner', '?')})")
    try:
        yield lk
    finally:
        lk.release()
