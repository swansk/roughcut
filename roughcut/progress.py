"""One shape for every long operation the board runs.

Karl, watching a render: *"got like no response — and just see rendering…"*, and later:
*"consider a progress tracking bar up top for anything which may take time to complete —
re-use across app."* The board had four long operations and four unrelated notions of
progress: the audio pass counted sidecars into `done`/`total`, the visual pass counted
two different things into two keys, a render counted parts on disk, and an Ask counted
nothing at all and showed an elapsed clock. Nothing could draw one bar for all of them
because there was no shape they shared.

This is that shape. A `Job` is a dict — deliberately, so the server's existing
`entry.update(...)`, `entry["done"] = n` and `{**entry}` keep working verbatim and this
is an addition rather than a rewrite — carrying:

    kind label state started detail pct eta_s milestones

and whatever per-kind keys the operation already had. Two sources of `pct`, in that
order of authority:

  * **milestones**, when the operation knows its own checkpoints. Each carries a
    `weight` and a `done_at`, and a running one may carry a `part` (0..1) so a long
    milestone — "the shots" of an Ask, "the coarse pass" of a visual run — moves the
    bar while it is still going rather than after it.
  * **counted work** otherwise: parts on disk, sidecars on disk. The filesystem is the
    honest progress bar, which is the lesson `_run_counted` was written from.

The bar never goes backwards (a count re-read from a filesystem can dip; a bar that
dips reads as a failure) and never reaches 100% before the job is finished.

`eta_s` is a **recalibrated** estimate, not the first guess repeated: each completed
milestone says how long that fraction of the work actually took, and the estimate is
re-derived from elapsed-vs-expected, trusting observation more the further in it is.
An estimate that says "2 minutes" for eight minutes running is worse than no estimate.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Sequence

TERMINAL = ("done", "failed", "cancelled")

# How far a purely time-based bar may creep between two milestones. Never all the way:
# arriving at the next milestone's boundary before the milestone itself has happened is
# the lie the whole recalibration exists to avoid.
CREEP_LIMIT = 0.85
# And a job with no milestones and no counter still must not sit at 100% while running.
RUNNING_CEILING = 99.0


def milestone(key: str, label: str, weight: float = 1.0) -> dict:
    """One checkpoint. `done_at` is filled in when it actually happens."""
    return {"key": str(key), "label": str(label), "weight": float(weight),
            "done_at": None, "part": 0.0}


def _clean_weight(w: Any) -> float:
    try:
        f = float(w)
    except (TypeError, ValueError):
        return 1.0
    return f if 0.0 < f < 1e6 else 1.0


class Job(dict):
    """One long operation, in the shape every part of the app reads.

    A dict subclass on purpose: the four job registries in app/server.py already held
    plain dicts and every call site mutates them by key. Subclassing keeps all of that
    working and adds the model on top, so the refactor cannot change what a job does.
    """

    def __init__(self, kind: str, label: str, *, id: str = "",
                 state: str = "running", detail: str = "",
                 eta_s: float | None = None,
                 milestones: Sequence[dict] | None = None, **extra: Any) -> None:
        super().__init__(
            id=id, kind=kind, label=label, state=state, started=time.time(),
            detail=detail, pct=0.0, eta_s=eta_s, estimated_s=eta_s,
            total_est_s=eta_s, eta_source="none", finished=None,
            milestones=[dict(m) for m in (milestones or [])],
            **extra)
        # Not in the dict: these must not be serialised into an API response.
        self.pct_fn: Callable[[Job], float | None] | None = None
        self._floor = 0.0

    # ------------------------------------------------------------ estimating

    def set_estimate(self, eta_s: float | None, milestones: Iterable[dict] | None,
                     *, source: str = "model", detail: str | None = None) -> None:
        """Adopt an estimate. The bar appears the moment this lands."""
        ms = [dict(m) for m in (milestones or [])]
        for m in ms:
            m.setdefault("done_at", None)
            m.setdefault("part", 0.0)
            m["weight"] = _clean_weight(m.get("weight"))
        self["milestones"] = ms
        if eta_s is not None:
            eta_s = float(eta_s)
        self["eta_s"] = eta_s
        self["estimated_s"] = eta_s
        self["total_est_s"] = eta_s
        self["eta_source"] = source
        if detail is not None:
            self["detail"] = detail

    # ------------------------------------------------------------- progress

    def _find(self, key: str) -> dict | None:
        for m in self["milestones"]:
            if m["key"] == key:
                return m
        return None

    def complete(self, key: str, *, detail: str | None = None) -> bool:
        """Mark a checkpoint reached. Everything before it is reached too.

        Out-of-order completion is not an error to shout about — a stream can skip a
        stage the model never bothered with — but leaving an earlier milestone open
        forever would park the bar, so reaching one closes its predecessors.
        """
        target = self._find(key)
        if target is None:
            return False
        now = time.time()
        for m in self["milestones"]:
            if m is target:
                break
            if m["done_at"] is None:
                m["done_at"] = now
                m["part"] = 1.0
        if target["done_at"] is None:
            target["done_at"] = now
        target["part"] = 1.0
        if detail is not None:
            self["detail"] = detail
        self._recalibrate()
        return True

    def advance(self, key: str, part: float, *, detail: str | None = None) -> bool:
        """How far through a milestone that is still running. 0..1, never backwards."""
        m = self._find(key)
        if m is None or m["done_at"] is not None:
            return False
        m["part"] = max(float(m.get("part") or 0.0), min(1.0, max(0.0, float(part))))
        if detail is not None:
            self["detail"] = detail
        return True

    def note(self, detail: str) -> None:
        self["detail"] = str(detail)

    def finish(self, state: str = "done", *, detail: str | None = None) -> None:
        now = time.time()
        if state == "done":
            for m in self["milestones"]:
                if m["done_at"] is None:
                    m["done_at"] = now
                m["part"] = 1.0
        self["state"] = state
        self["finished"] = now
        if detail is not None:
            self["detail"] = detail

    # ---------------------------------------------------------------- maths

    def elapsed(self) -> float:
        end = self.get("finished") or time.time()
        return max(0.0, end - self["started"])

    def _weights(self) -> tuple[float, float, float]:
        """(done weight, in-flight weight, total weight)."""
        total = done = part = 0.0
        for m in self["milestones"]:
            w = _clean_weight(m.get("weight"))
            total += w
            if m["done_at"] is not None:
                done += w
            else:
                part += w * min(1.0, max(0.0, float(m.get("part") or 0.0)))
        return done, part, total

    def fraction(self) -> float | None:
        """How much of the work is behind us, from milestones only. None if unknown."""
        done, part, total = self._weights()
        if total <= 0:
            return None
        return min(1.0, (done + part) / total)

    def current(self) -> dict | None:
        for m in self["milestones"]:
            if m["done_at"] is None:
                return m
        return None

    def _recalibrate(self) -> None:
        """Re-derive the ETA from what the work has actually cost so far.

        The first estimate is a guess made before anything ran. Every completed
        milestone replaces part of it with measurement, weighted by how much of the
        job that measurement covers — early evidence is thin, late evidence is nearly
        the whole answer.
        """
        done, _part, total = self._weights()
        if total <= 0 or done <= 0:
            return
        frac = done / total
        elapsed = self.elapsed()
        observed_total = elapsed / frac
        first = self.get("estimated_s")
        if first is None:
            total_est = observed_total
        else:
            trust = min(1.0, frac * 1.5)
            total_est = trust * observed_total + (1.0 - trust) * float(first)
        self["total_est_s"] = round(total_est, 1)
        self["eta_source"] = "measured"

    def remaining(self) -> float | None:
        """Seconds left, as best anyone knows. None when nothing has estimated it."""
        if self["state"] == "done":
            return 0.0
        total_est = self.get("total_est_s")
        if not total_est or float(total_est) <= 0:
            return None
        return max(0.0, float(total_est) - self.elapsed())

    def _time_pct(self) -> float | None:
        """The bar the clock alone would draw."""
        total_est = self.get("total_est_s")
        if not total_est or float(total_est) <= 0:
            return None
        return 100.0 * self.elapsed() / float(total_est)

    def percent(self) -> float:
        if self["state"] == "done":
            return 100.0
        frac = self.fraction()
        if frac is not None:
            base = 100.0 * frac
            # Creep toward the next checkpoint on the clock, so a long milestone is
            # not a stalled bar — but stop short of it, because arriving is the
            # milestone's job.
            nxt = self.current()
            if nxt is not None:
                _d, _p, total = self._weights()
                room = 100.0 * _clean_weight(nxt.get("weight")) / total
                room *= 1.0 - min(1.0, float(nxt.get("part") or 0.0))
                t = self._time_pct()
                if t is not None:
                    base = max(base, min(t, base + room * CREEP_LIMIT))
            pct = base
        elif self.pct_fn is not None:
            got = self.pct_fn(self)
            pct = float(got) if got is not None else (self._time_pct() or 0.0)
        else:
            pct = self._time_pct() or 0.0
        pct = min(RUNNING_CEILING, max(0.0, pct))
        self._floor = max(self._floor, pct)          # a bar that dips reads as failure
        return self._floor

    # ------------------------------------------------------------- snapshot

    def snapshot(self) -> dict:
        """What the API hands out. A plain dict: every key this job has ever had,
        plus the shared ones, so no existing reader loses a field."""
        pct = self.percent()
        left = self.remaining()
        self["pct"] = round(pct, 1)
        self["eta_s"] = None if left is None else round(left, 1)
        out = dict(self)
        cur = self.current()
        done = sum(1 for m in self["milestones"] if m["done_at"] is not None)
        out.update(
            elapsed_s=round(self.elapsed(), 1),
            pct=round(pct, 1),
            eta_s=self["eta_s"],
            milestone=(cur or {}).get("label", ""),
            milestone_key=(cur or {}).get("key", ""),
            milestones_done=done,
            milestones_total=len(self["milestones"]),
        )
        return out


def counted(done_key: str = "done", total_key: str = "total",
            ceiling: float = RUNNING_CEILING) -> Callable[[Job], float | None]:
    """A `pct_fn` reading two counters the job already keeps."""
    def fn(job: Job) -> float | None:
        total = float(job.get(total_key) or 0)
        if total <= 0:
            return None
        return min(ceiling, 100.0 * float(job.get(done_key) or 0) / total)
    return fn


# How long a finished job stays on the strip. Long enough to be read as "done" rather
# than as having vanished mid-sentence; short enough that an idle board is an empty one.
KEEP_FINISHED_S = 12.0


def live(jobs: Iterable[Job], *, keep_finished_s: float | None = None) -> list[dict]:
    """Everything worth showing: what is running, and what just stopped.

    Finished jobs linger briefly so the bar can say "done" rather than vanishing
    mid-sentence, then the strip empties itself.
    """
    keep = KEEP_FINISHED_S if keep_finished_s is None else keep_finished_s
    now = time.time()
    out = []
    for job in jobs:
        if job["state"] in TERMINAL:
            fin = job.get("finished")
            if fin is None or now - fin > keep:
                continue
        out.append(job.snapshot())
    out.sort(key=lambda s: s["started"])
    return out
