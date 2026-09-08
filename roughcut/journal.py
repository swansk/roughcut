"""The index as a journal, not a job.

Karl's decision 3 (docs/INTAKE.md): the index runs unattended and resumable, in a
priority order, and releases clips whole. The design (docs/design/cutting-room-floor.html
§3) makes that a **journal** and a **queue**: one file per bin under `--work`, holding for
every clip the state of each stage, with attempts and cost; a resume path that is the
same operation as "I added three clips" — index what isn't done.

Two rules everything here is written from:

  * **The sidecars on disk are the truth; the journal is the plan.** `reconcile()` takes
    a description of what exists per clip and bends the plan to it: a stage whose file
    exists is done whatever the journal said, and a stage the journal thought was running
    with nothing on disk is a crash — it goes back to the queue with its attempts kept.
    Cost is only ever recorded by `finish()`, so a crash mid-sheet costs the sheet the
    server re-reads and the journal never double-counts it.
  * **Never over-index telemetry.** `score()` weighs the speech candidates and theme hits
    most and the telemetry peaks least, with duration as a small tiebreak. Telemetry
    could be noisy or bad (INTAKE M6); until R11 measures it, it barely moves the order.

This module is pure: it never stats the disk, never runs a tool, never imports the
server. The server (INTAKE I3.2) builds the `files` dict from its sidecar dirs, hands the
free-stage facts in, asks `next()` what to run, and reports back with `start` / `finish`
/ `fail`. The only I/O is the journal file itself, written atomically.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path
from typing import Any, Iterable

VERSION = 1

# The stages, in the order a clip walks through them.
STAGES = ("probe", "telemetry", "asr", "proxy", "look", "close", "picks")

# What a stage needs finished (or skipped) before it may start. Proxy needs the probe,
# not the ASR: encoding a preview does not wait on listening. Picks wait on the close
# look as well as the sheets, because picks are derived from the events file and the
# events file folds the close look in — releasing a clip whose picks predate its close
# look is exactly the "picks might still change" the design forbids. A skipped close
# look (nothing worth looking at closely) satisfies the dependency.
DEPENDS: dict[str, tuple[str, ...]] = {
    "probe": (),
    "telemetry": ("probe",),
    "asr": ("probe",),
    "proxy": ("probe",),
    "look": ("proxy",),
    "close": ("look",),
    "picks": ("asr", "look", "close"),
}

# The stages that spend money. `pause_priced()` holds exactly these.
PRICED = frozenset({"look", "close"})

# Which worker pool a stage draws from. Proxies and sheets have separate limits because
# one is CPU-bound ffmpeg and the other is model calls with a rate limit; running two
# encodes at once is a machine question, running two sheets at once is a budget one.
KIND: dict[str, str] = {
    "probe": "probe", "telemetry": "probe", "asr": "asr", "proxy": "proxy",
    "look": "sheet", "close": "sheet", "picks": "picks",
}
DEFAULT_WORKERS: dict[str, int] = {"probe": 2, "asr": 1, "proxy": 1, "sheet": 2, "picks": 1}

# States a stage can be in. `failed` is retryable; `parked` is not, until someone
# `unpark`s it. `skipped` means not applicable (telemetry on a phone clip) and counts
# as done for release and dependencies.
STATES = ("queued", "running", "done", "failed", "skipped", "parked")
RETRYABLE = ("queued", "failed")
SETTLED = ("done", "skipped")

MAX_ATTEMPTS = 3          # the design: "a clip that fails a stage three times is parked"
TIMING_WINDOW = 8         # how many recent durations the per-stage rolling mean keeps

ORDERS = ("priority", "capture")


class JournalError(ValueError):
    """A call the journal cannot honour: an unknown clip or stage, a stage started out
    of order. Raised rather than swallowed because every one of these is a server bug
    the unattended run would otherwise carry silently for hours."""


# ---------------------------------------------------------------------- priority
#
# The priority score, 0–1, from the facts the free stages produce. Linear saturation
# per term (`min(1, x / full)`) so the formula can be read off the constants and a
# clip cannot buy the top of the list with one absurd number.
#
#   score = 0.40 · sat(candidates, 10)     R8 speech candidates — the strongest signal
#         + 0.30 · sat(theme_hits, 3)      the editor said what the film is about
#         + 0.15 · sat(words, 300)         how much was said at all
#         + 0.10 · sat(duration_s, 300)    a small tiebreak: more footage, more chances
#         + 0.05 · sat(telemetry_peaks, 5) least, per Karl's rule — unmeasured until R11
#
# Candidates and theme hits together are 70% of the score; telemetry alone can move a
# clip by at most 0.05, which is less than one extra candidate (0.04 each) or one theme
# hit (0.10). A missing fact is 0, so a clip without telemetry loses nothing it could
# have had from the other terms.
WEIGHTS: dict[str, tuple[float, float]] = {
    # fact: (weight, value at which the term saturates)
    "candidates": (0.40, 10.0),
    "theme_hits": (0.30, 3.0),
    "words": (0.15, 300.0),
    "duration_s": (0.10, 300.0),
    "telemetry_peaks": (0.05, 5.0),
}


def score(facts: dict | None) -> float:
    """0–1 priority from `{"words", "candidates", "telemetry_peaks", "theme_hits",
    "duration_s"}`. Missing or unparseable facts count as zero."""
    total = 0.0
    for key, (weight, full) in WEIGHTS.items():
        try:
            v = float((facts or {}).get(key) or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        total += weight * min(1.0, max(0.0, v) / full)
    return round(min(1.0, total), 4)


# ------------------------------------------------------------------- the records

def _stage_record() -> dict:
    return {"state": "queued", "attempts": 0, "last_error": None, "cost_usd": 0.0,
            "started": None, "finished": None, "not_before": None}


def _clip_record(clip: str, now: float, captured: float | None) -> dict:
    return {"clip": clip, "added": now, "captured": captured, "facts": None,
            "priority": None, "missing": False, "parked": None,
            "stages": {s: _stage_record() for s in STAGES}}


class Journal:
    """One bin's index plan, persisted as JSON, written atomically.

    Every mutating method saves when `path` is set, so the file on disk is never more
    than one call behind the process — which is what makes a crash resumable rather
    than a mystery. `now` is injectable everywhere for tests.
    """

    def __init__(self, path: Path | str | None = None, *, bin: str = "",
                 workers: dict[str, int] | None = None, order: str = "priority",
                 autosave: bool = True) -> None:
        self.path = Path(path) if path else None
        self.autosave = autosave
        self.data: dict[str, Any] = {
            "version": VERSION, "bin": bin or (self.path.stem if self.path else ""),
            "created": time.time(), "updated": None,
            "order": order if order in ORDERS else "priority",
            "workers": {**DEFAULT_WORKERS, **(workers or {})},
            "paused_priced": False,
            "clips": {},
            "timing": {s: [] for s in STAGES},
            "log": [],
        }

    # ------------------------------------------------------------ persistence

    @classmethod
    def load(cls, path: Path | str, **kw: Any) -> "Journal":
        """The journal at `path`, or a fresh one there if none exists yet.

        A corrupt file raises — silently starting over would lose every attempt count
        and every dollar recorded, and the sidecars would still be there to rebuild
        from once somebody has looked at what happened.
        """
        path = Path(path)
        j = cls(path, **kw)
        if not path.exists():
            return j
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            raise JournalError(f"journal at {path} is unreadable: {e}") from e
        if not isinstance(data, dict) or "clips" not in data:
            raise JournalError(f"journal at {path} is not a journal")
        j.data.update(data)
        j.data["workers"] = {**DEFAULT_WORKERS, **(data.get("workers") or {})}
        j.data.setdefault("timing", {})
        for s in STAGES:
            j.data["timing"].setdefault(s, [])
        # A stage added to STAGES after this journal was written starts queued.
        for rec in j.data["clips"].values():
            rec.setdefault("stages", {})
            for s in STAGES:
                rec["stages"].setdefault(s, _stage_record())
                rec["stages"][s].setdefault("not_before", None)
            rec.setdefault("missing", False)
            rec.setdefault("parked", None)
            rec.setdefault("facts", None)
            rec.setdefault("priority", None)
            rec.setdefault("captured", None)
        return j

    def save(self) -> None:
        """Temp file beside the journal, then an atomic replace: a crash during the
        write leaves the previous journal intact, never a half-written one."""
        if self.path is None:
            return
        self.data["updated"] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _touch(self) -> None:
        if self.autosave:
            self.save()

    def _log(self, now: float, what: str) -> None:
        log = self.data.setdefault("log", [])
        log.append({"at": now, "what": what})
        del log[:-200]

    # --------------------------------------------------------------- lookups

    @property
    def clips(self) -> dict[str, dict]:
        return self.data["clips"]

    @property
    def order(self) -> str:
        return self.data["order"]

    @property
    def workers(self) -> dict[str, int]:
        return self.data["workers"]

    @property
    def paused_priced(self) -> bool:
        return bool(self.data.get("paused_priced"))

    def _rec(self, clip: str) -> dict:
        try:
            return self.clips[clip]
        except KeyError:
            raise JournalError(f"unknown clip {clip!r}") from None

    def _stage(self, clip: str, stage: str) -> dict:
        if stage not in STAGES:
            raise JournalError(f"unknown stage {stage!r}")
        return self._rec(clip)["stages"][stage]

    def state(self, clip: str, stage: str) -> str:
        return self._stage(clip, stage)["state"]

    def priority(self, clip: str) -> float | None:
        """The clip's score, or None until its free stages have reported."""
        return self._rec(clip).get("priority")

    # ---------------------------------------------------------------- clips

    def add_clips(self, clips: Iterable[str], captured: dict[str, float] | None = None,
                  *, now: float | None = None) -> list[str]:
        """Footage added at any time (decision 4). New clips enter queued for every
        stage; a clip already known is un-marked missing and otherwise untouched, so
        re-adding a folder never re-runs anything. Returns the genuinely new names."""
        now = time.time() if now is None else now
        captured = captured or {}
        new: list[str] = []
        for clip in clips:
            if clip in self.clips:
                rec = self.clips[clip]
                if rec.get("missing"):
                    rec["missing"] = False
                    self._log(now, f"{clip} is back")
                if captured.get(clip) is not None:
                    rec["captured"] = captured[clip]
                continue
            self.clips[clip] = _clip_record(clip, now, captured.get(clip))
            new.append(clip)
        if new:
            self._log(now, f"added {len(new)} clip(s): {', '.join(new)}")
        self._touch()
        return new

    def remove_missing(self, clips: Iterable[str], *, now: float | None = None) -> list[str]:
        """Footage that is gone. Marked, never deleted: the record keeps its cost and
        its history, and a keep pointing at it is flagged, not lost (design §3)."""
        now = time.time() if now is None else now
        marked = []
        for clip in clips:
            rec = self._rec(clip)
            if not rec.get("missing"):
                rec["missing"] = True
                rec["missing_at"] = now
                marked.append(clip)
        if marked:
            self._log(now, f"missing: {', '.join(marked)}")
        self._touch()
        return marked

    def set_captured(self, clip: str, captured: float | None) -> None:
        self._rec(clip)["captured"] = captured
        self._touch()

    def set_facts(self, clip: str, facts: dict | None) -> float:
        """The free-stage facts for a clip; the priority follows from them."""
        rec = self._rec(clip)
        rec["facts"] = dict(facts or {})
        rec["priority"] = score(rec["facts"])
        self._touch()
        return rec["priority"]

    def set_order(self, order: str) -> None:
        if order not in ORDERS:
            raise JournalError(f"order must be one of {ORDERS}, not {order!r}")
        self.data["order"] = order
        self._touch()

    def set_workers(self, workers: dict[str, int]) -> None:
        self.data["workers"] = {**DEFAULT_WORKERS, **workers}
        self._touch()

    def skip(self, clip: str, stage: str, reason: str = "", *,
             now: float | None = None) -> None:
        """Not applicable — telemetry on a clip with no sensor stream, a close look on
        a clip with nothing worth looking at. Counts as done for release."""
        now = time.time() if now is None else now
        st = self._stage(clip, stage)
        st["state"] = "skipped"
        st["last_error"] = reason or None
        st["finished"] = now
        self._touch()

    def unpark(self, clip: str, *, now: float | None = None) -> None:
        """A human looked, and wants another three tries."""
        now = time.time() if now is None else now
        rec = self._rec(clip)
        parked = rec.get("parked")
        if parked:
            st = rec["stages"][parked["stage"]]
            st["state"] = "queued"
            st["attempts"] = 0
            st["not_before"] = None
        rec["parked"] = None
        self._log(now, f"{clip} unparked")
        self._touch()

    # ----------------------------------------------------------- reconcile

    def reconcile(self, files: dict[str, dict[str, bool]], *,
                  now: float | None = None) -> dict:
        """Bend the plan to the disk. `files` is `{clip: {stage: exists}}` for the
        stages that leave a file (`asr`, `proxy`, `look`, `close`, `picks`; `probe` and
        `telemetry` may be included when the server records them somewhere).

        Per stage the server reports on:
          * file exists          → `done`, whatever the journal said
          * no file, was running → a crash: `queued`, attempts kept, error noted
          * no file, was done    → somebody deleted it: `queued` (files are truth)
        A stage the server does not report on (no key) is only touched if it was
        `running`: nothing survives a restart mid-stage, so it is re-queued.

        Returns what changed, for the log line the design shows ("crashed 14:02
        during CLIP_07 sheet 3 · resumed 14:04 · 1 sheet re-read").
        """
        now = time.time() if now is None else now
        out: dict[str, list] = {"requeued": [], "found": [], "lost": []}
        for clip, rec in self.clips.items():
            reported = files.get(clip) or {}
            for stage in STAGES:
                st = rec["stages"][stage]
                if stage in reported:
                    exists = bool(reported[stage])
                    if exists and st["state"] != "done":
                        if st["state"] != "skipped":
                            st["state"] = "done"
                            st["finished"] = st["finished"] or now
                            st["not_before"] = None
                            if (rec.get("parked") or {}).get("stage") == stage:
                                rec["parked"] = None     # the file is the verdict
                            out["found"].append((clip, stage))
                        continue
                    if not exists and st["state"] == "running":
                        st["state"] = "queued"
                        st["last_error"] = "crashed mid-stage: no output on disk"
                        st["started"] = None
                        out["requeued"].append((clip, stage))
                    elif not exists and st["state"] == "done":
                        st["state"] = "queued"
                        st["finished"] = None
                        st["last_error"] = "output missing from disk"
                        out["lost"].append((clip, stage))
                elif st["state"] == "running":
                    st["state"] = "queued"
                    st["last_error"] = "crashed mid-stage"
                    st["started"] = None
                    out["requeued"].append((clip, stage))
        if out["requeued"] or out["lost"]:
            self._log(now, "reconciled: "
                      + ", ".join(f"{c} {s} re-queued" for c, s in out["requeued"])
                      + ("; " if out["requeued"] and out["lost"] else "")
                      + ", ".join(f"{c} {s} lost" for c, s in out["lost"]))
        self._touch()
        return out

    # ------------------------------------------------------------- the queue

    def _sort_key(self, rec: dict):
        cap = rec.get("captured")
        cap = float("inf") if cap is None else float(cap)
        if self.order == "capture":
            return (cap, rec["clip"])
        pri = rec.get("priority")
        # Scored clips first, best first; unscored after them in capture order — but
        # note that an unscored clip's *free* stages draw from pools the scored clips
        # have long finished with, so in practice they run at once.
        return (0 if pri is not None else 1, -(pri or 0.0), cap, rec["clip"])

    def ordered(self) -> list[str]:
        """Every clip in the order the queue walks them (missing ones included, last)."""
        recs = sorted(self.clips.values(), key=self._sort_key)
        return ([r["clip"] for r in recs if not r.get("missing")]
                + [r["clip"] for r in recs if r.get("missing")])

    def _deps_met(self, rec: dict, stage: str) -> bool:
        return all(rec["stages"][d]["state"] in SETTLED for d in DEPENDS[stage])

    def running(self) -> list[tuple[str, str]]:
        return [(c, s) for c, rec in self.clips.items()
                for s in STAGES if rec["stages"][s]["state"] == "running"]

    def _pool_load(self) -> dict[str, int]:
        load: dict[str, int] = {}
        for _c, s in self.running():
            load[KIND[s]] = load.get(KIND[s], 0) + 1
        return load

    def next(self, *, now: float | None = None,
             workers: dict[str, int] | None = None) -> tuple[str, str] | None:
        """The next (clip, stage) to run, or None when nothing is runnable right now.

        Walks clips in the journal's order and stages in pipeline order, so the top
        clip's next stage always comes before the second clip's first — that is what
        releases clips one at a time instead of half-indexing all of them. Honours
        dependencies, the per-pool worker limits, `not_before` backoffs, the priced
        pause, and skips parked or missing clips.
        """
        now = time.time() if now is None else now
        limits = {**self.workers, **(workers or {})}
        load = self._pool_load()
        for clip in self.ordered():
            rec = self.clips[clip]
            if rec.get("missing") or rec.get("parked"):
                continue
            for stage in STAGES:
                st = rec["stages"][stage]
                if st["state"] not in RETRYABLE:
                    continue
                if self.paused_priced and stage in PRICED:
                    continue
                if st.get("not_before") and float(st["not_before"]) > now:
                    continue
                if not self._deps_met(rec, stage):
                    continue
                kind = KIND[stage]
                if load.get(kind, 0) >= int(limits.get(kind, 1)):
                    continue
                return clip, stage
        return None

    def start(self, clip: str, stage: str, *, now: float | None = None) -> None:
        """Mark a stage running. Counts as an attempt, so a stage that crashes the
        process three times parks instead of looping forever."""
        now = time.time() if now is None else now
        rec = self._rec(clip)
        st = self._stage(clip, stage)
        if st["state"] not in RETRYABLE:
            raise JournalError(f"{clip} {stage} is {st['state']}, not startable")
        if rec.get("parked"):
            raise JournalError(f"{clip} is parked ({rec['parked'].get('error')})")
        if not self._deps_met(rec, stage):
            waiting = [d for d in DEPENDS[stage] if rec["stages"][d]["state"] not in SETTLED]
            raise JournalError(f"{clip} {stage} waits on {', '.join(waiting)}")
        st["state"] = "running"
        st["attempts"] = int(st.get("attempts") or 0) + 1
        st["started"] = now
        st["finished"] = None
        st["not_before"] = None
        self._touch()

    def finish(self, clip: str, stage: str, cost_usd: float = 0.0, *,
               now: float | None = None) -> None:
        """A stage completed. The only place cost is recorded, and the only place a
        duration is measured — so a re-run after a crash records once, not twice."""
        now = time.time() if now is None else now
        st = self._stage(clip, stage)
        was_running = st["state"] == "running" and st.get("started") is not None
        if was_running:
            self._note_duration(stage, max(0.0, now - float(st["started"])))
        st["state"] = "done"
        st["finished"] = now
        st["cost_usd"] = round(float(st.get("cost_usd") or 0.0) + float(cost_usd or 0.0), 4)
        st["last_error"] = None
        st["not_before"] = None
        if self.released(clip):
            self._log(now, f"{clip} released")
        self._touch()

    def fail(self, clip: str, stage: str, error: str = "", *, cost_usd: float = 0.0,
             retry_after_s: float | None = None, now: float | None = None) -> str:
        """A stage failed. Retryable until `MAX_ATTEMPTS`, then the clip is parked with
        the reason and everything else continues (design §3: failure is a state, not a
        stop). `retry_after_s` is the server's backoff for a rate-limited sheet; `next()`
        will not offer the stage again before then. Money spent on a failed call is
        still money — record it. Returns the stage's new state."""
        now = time.time() if now is None else now
        rec = self._rec(clip)
        st = self._stage(clip, stage)
        st["last_error"] = str(error) if error else "failed"
        st["cost_usd"] = round(float(st.get("cost_usd") or 0.0) + float(cost_usd or 0.0), 4)
        st["finished"] = now
        if st["state"] != "running":
            # A failure reported for a stage nobody started still counts as one try.
            st["attempts"] = int(st.get("attempts") or 0) + 1
        st["started"] = None
        if st["attempts"] >= MAX_ATTEMPTS:
            st["state"] = "parked"
            st["not_before"] = None
            rec["parked"] = {"stage": stage, "error": st["last_error"], "at": now,
                             "attempts": st["attempts"]}
            self._log(now, f"{clip} parked at {stage} after {st['attempts']} tries: "
                           f"{st['last_error']}")
        else:
            st["state"] = "failed"
            st["not_before"] = (now + float(retry_after_s)) if retry_after_s else None
        self._touch()
        return st["state"]

    # ------------------------------------------------------------- the budget

    def pause_priced(self, reason: str = "budget cap", *, now: float | None = None) -> None:
        """Hold the priced stages; the free ones and anything already running go on.
        The floor opens on what is released."""
        now = time.time() if now is None else now
        if not self.paused_priced:
            self.data["paused_priced"] = True
            self.data["paused_reason"] = reason
            self._log(now, f"priced stages paused: {reason}")
        self._touch()

    def resume_priced(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if self.paused_priced:
            self.data["paused_priced"] = False
            self.data["paused_reason"] = None
            self._log(now, "priced stages resumed")
        self._touch()

    # -------------------------------------------------------------- release

    def released(self, clip: str) -> bool:
        """Every applicable stage done (or skipped) and the footage present. All or
        nothing: the floor never shows a clip whose picture can't play or whose picks
        might still change."""
        rec = self._rec(clip)
        if rec.get("missing"):
            return False
        return all(rec["stages"][s]["state"] in SETTLED for s in STAGES)

    def released_clips(self) -> list[str]:
        return [c for c in self.ordered() if self.released(c)]

    # ------------------------------------------------------------- progress

    def _note_duration(self, stage: str, seconds: float) -> None:
        recent = self.data["timing"].setdefault(stage, [])
        recent.append(round(seconds, 3))
        del recent[:-TIMING_WINDOW]

    def mean_duration(self, stage: str) -> float | None:
        """Rolling mean of the last `TIMING_WINDOW` measured runs, or None unmeasured."""
        recent = self.data["timing"].get(stage) or []
        return statistics.fmean(recent) if recent else None

    def cost_usd(self) -> float:
        return round(sum(float(st.get("cost_usd") or 0.0)
                         for rec in self.clips.values()
                         for st in rec["stages"].values()), 4)

    def progress(self, *, now: float | None = None) -> dict:
        """The honest bar: per-stage counts, released count, cost so far, and an ETA
        from measured durations only — never a guess dressed as a measurement.

        The ETA is the busiest worker pool's remaining seconds: for each pool, the
        stage-seconds still to run (queued and failed stages at their rolling mean; a
        running stage at what is left of its mean) divided by that pool's workers, and
        the largest of those is the answer. Stages without a measurement yet are
        listed in `eta_unmeasured` rather than pretending to be free.
        """
        now = time.time() if now is None else now
        counts = {s: {st: 0 for st in STATES} for s in STAGES}
        pool_s: dict[str, float] = {}
        unmeasured: set[str] = set()
        clips_n = released_n = missing_n = parked_n = 0
        done_stages = total_stages = 0
        rows = []
        for clip in self.ordered():
            rec = self.clips[clip]
            clips_n += 1
            if rec.get("missing"):
                missing_n += 1
                state = "missing"
            elif rec.get("parked"):
                parked_n += 1
                state = "parked"
            elif self.released(clip):
                released_n += 1
                state = "released"
            elif any(rec["stages"][s]["state"] == "running" for s in STAGES):
                state = "indexing"
            else:
                state = "queued"
            for s in STAGES:
                st = rec["stages"][s]
                counts[s][st["state"]] += 1
                if rec.get("missing"):
                    continue
                total_stages += 1
                if st["state"] in SETTLED:
                    done_stages += 1
                    continue
                if st["state"] == "parked" or rec.get("parked"):
                    continue
                if self.paused_priced and s in PRICED:
                    continue
                mean = self.mean_duration(s)
                if mean is None:
                    unmeasured.add(s)
                    continue
                left = mean
                if st["state"] == "running" and st.get("started") is not None:
                    left = max(0.0, mean - (now - float(st["started"])))
                pool_s[KIND[s]] = pool_s.get(KIND[s], 0.0) + left
            rows.append({"clip": clip, "priority": rec.get("priority"), "state": state,
                         "captured": rec.get("captured"),
                         "stages": {s: rec["stages"][s]["state"] for s in STAGES},
                         "attempts": {s: rec["stages"][s]["attempts"] for s in STAGES
                                      if rec["stages"][s]["attempts"] > 1
                                      or rec["stages"][s]["state"] in ("failed", "parked")},
                         "error": (rec.get("parked") or {}).get("error")
                                  or next((rec["stages"][s]["last_error"] for s in STAGES
                                           if rec["stages"][s]["state"] == "failed"), None)})
        eta_by_kind = {k: round(v / max(1, int(self.workers.get(k, 1))), 1)
                       for k, v in pool_s.items()}
        eta_s = round(max(eta_by_kind.values()), 1) if eta_by_kind else None
        return {
            "clips": clips_n, "released": released_n, "missing": missing_n,
            "parked": parked_n, "running": self.running(),
            "stages": counts,
            "done_stages": done_stages, "total_stages": total_stages,
            "pct": round(100.0 * done_stages / total_stages, 1) if total_stages else 0.0,
            "cost_usd": self.cost_usd(),
            "eta_s": eta_s, "eta_by_kind": eta_by_kind,
            "eta_unmeasured": sorted(unmeasured),
            "eta_source": "measured" if eta_by_kind else "none",
            "mean_s": {s: (round(m, 2) if m is not None else None)
                       for s in STAGES for m in [self.mean_duration(s)]},
            "paused_priced": self.paused_priced,
            "paused_reason": self.data.get("paused_reason"),
            "order": self.order, "workers": dict(self.workers),
            "rows": rows,
            "log": list(self.data.get("log") or [])[-20:],
        }
