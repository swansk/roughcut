"""Has anything the board reads changed on disk? Answered without touching the disk.

Redesign wave 11, lane CHEAP (verdict 8, punch 10): `/api/flow`, `/api/status` and
`/api/firstpass` re-read a few hundred files per poll. Each read drops Python's GIL and
waits up to a switch interval to get it back, so with the server's own busy threads
(an export, the proxy walk, a playing Program's range reads) a 15 ms answer took
0.5-1.5 s. The server now answers them from a snapshot and rebuilds it only when this
says something it reads has changed.

An inotify watch (Linux; CLAUDE.md: Linux-first) on each directory the snapshot reads,
with the names in it that matter (`*.audio.json` in the sidecars folder, not the peaks
files beside them; a proxy's final name, not its `.part` file). `changes()` drains the
queue with one non-blocking read: the kernel queued every event the moment the write
happened, so a file written a microsecond before the poll is already in it — a
snapshot can never be served stale, the way a timer could. A target that does not
exist yet is watched through its nearest existing parent, for the name that leads to
it; `arm()` again after a change moves the watch down.

Without inotify (`available` False) `changes()` returns None: the caller must treat
every call as a change (correct, and as slow as before).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import fnmatch
import os
import struct
import threading
from pathlib import Path

IN_MODIFY = 0x002
IN_CLOSE_WRITE = 0x008
IN_MOVED_FROM = 0x040
IN_MOVED_TO = 0x080
IN_CREATE = 0x100
IN_DELETE = 0x200
IN_DELETE_SELF = 0x400
IN_MOVE_SELF = 0x800
IN_Q_OVERFLOW = 0x4000
IN_IGNORED = 0x8000
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000
# IN_MODIFY is left out on purpose: an encode writing its file fires it per block; the
# close (IN_CLOSE_WRITE) and the rename that publishes a file are what change a fact.
MASK = (IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE
        | IN_DELETE_SELF | IN_MOVE_SELF)
EVENT = struct.Struct("iIII")
# names no fact is ever read from: the temp files writers publish by rename
IGNORE = ("*.part", "*.part.*", "*.tmp", "*.tmp.*", ".*")


class DirWatch:
    """Watches (dir, name patterns) targets; `changes()` lists what changed since."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.fd: int | None = None
        self.wds: dict[int, tuple[Path, tuple[str, ...]]] = {}
        try:
            libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
            self._add = libc.inotify_add_watch
            self._add.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            self._rm = libc.inotify_rm_watch
            self._rm.argtypes = [ctypes.c_int, ctypes.c_int]
            fd = libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
            self.fd = fd if fd >= 0 else None
        except (OSError, AttributeError):
            self.fd = None

    @property
    def available(self) -> bool:
        return self.fd is not None

    def arm(self, targets: list[tuple[Path, tuple[str, ...]]]) -> None:
        """Watch exactly these targets: a directory and the names in it that matter
        (fnmatch patterns). A missing directory is watched through its nearest existing
        parent, for the one name on the way down to it."""
        if self.fd is None:
            return
        want: dict[Path, set[str]] = {}
        for d, pats in targets:
            d = Path(d)
            p = d
            while not p.is_dir() and p != p.parent:
                want.setdefault(p.parent, set()).add(p.name)
                p = p.parent
            if p == d:
                want.setdefault(d, set()).update(pats)
        with self.lock:
            # a folder already watched keeps its watch (its number): an event queued on
            # it and not yet drained is still read
            wds: dict[int, tuple[Path, tuple[str, ...]]] = {}
            for d, pats in want.items():
                wd = self._add(self.fd, os.fsencode(str(d)), MASK)
                if wd >= 0:
                    old = wds.get(wd, (d, ()))[1]
                    wds[wd] = (d, tuple(sorted(set(old) | pats)))
            for wd in set(self.wds) - set(wds):
                self._rm(self.fd, wd)
            self.wds = wds

    def _drain(self) -> list[tuple[int, int, str]]:
        out: list[tuple[int, int, str]] = []
        while True:
            try:
                buf = os.read(self.fd, 65536)
            except BlockingIOError:
                return out
            except OSError:
                return out
            if not buf:
                return out
            off = 0
            while off + EVENT.size <= len(buf):
                wd, mask, _cookie, n = EVENT.unpack_from(buf, off)
                name = buf[off + EVENT.size: off + EVENT.size + n].rstrip(b"\0")
                off += EVENT.size + n
                out.append((wd, mask, os.fsdecode(name)))

    def changes(self, blocking: bool = True) -> list[tuple[Path, str]] | None:
        """The (directory, name) pairs that changed since the last call, filtered by
        the targets' patterns; `(dir, "")` for a watched directory itself going away
        or the queue overflowing. None when there is no inotify — or, `blocking`
        False, when `arm` holds the watch right now (the caller cannot know: a miss)."""
        if self.fd is None:
            return None
        if not self.lock.acquire(blocking=blocking):
            return None
        try:
            raw = self._drain()
            out: list[tuple[Path, str]] = []
            for wd, mask, name in raw:
                if mask & IN_Q_OVERFLOW:
                    out.append((Path("/"), ""))
                    continue
                if mask & IN_IGNORED:
                    continue
                d, pats = self.wds.get(wd, (None, ()))
                if d is None:
                    continue
                if mask & (IN_DELETE_SELF | IN_MOVE_SELF):
                    out.append((d, ""))
                    continue
                if any(fnmatch.fnmatchcase(name, g) for g in IGNORE):
                    continue
                if any(fnmatch.fnmatchcase(name, p) for p in pats):
                    out.append((d, name))
            return out
        finally:
            self.lock.release()
