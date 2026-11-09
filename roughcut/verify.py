"""Claude as the checker: no unsupported event claims reach the planner or the board.

Wave 20, lane VERIFY. The bake-off (research/R12-understanding) decided who does what:
telemetry decides *when* (roughcut.telemetry_events), a local model settles what it can
(roughcut.localvlm, when a GPU stack is there), and **Claude checks, it does not scan**:
one levelled 8 fps contact strip of ~24 cells plus the event's facts per call, answering
confirm / reject / retime / unsure. Sonnet is enough (Opus showed no gain at 2x).
On Karl's checked Killington set: 15 of 17 windows right, 6 of 7 airs, 4 of 4 falls, 5 of
6 old false claims rejected.

Everything here goes through `roughcut.inference` (role `verify`, `isolated` CLI call: no
MCP servers, Read tool only). The price is shown before any batch, every call lands in
the ledger, and a verdict already stored is never bought twice.

## Per-bin mode (stored with the bin's index, `work/index/<bin>.verify.json`)

    hybrid   (default) telemetry + local checks + Claude on what matters (routing below)
    local    no Claude calls at all; events from telemetry and local checks, all marked
             `certainty: "unchecked"`
    claude   today's pipeline + this checker: no local model, Claude settles any
             checkable event (still capped per run)

`GET /api/index/mode` / `PUT /api/index/mode {mode}` -> `mode_payload()`:

    {"mode": "hybrid" | "local" | "claude",          # what the editor chose
     "modes": [...], "default": "hybrid",
     "local_available": bool,                         # a GPU stack is installed and usable
     "effective": "hybrid" | "local" | "claude",      # what actually runs
     "note": "plain words, e.g. 'No local model here, so Hybrid runs as Claude only ...'"}

`effective` differs from `mode` only for Hybrid without a local stack: it behaves like
Claude only for the local half, and `note` says so.

## Routing in Hybrid (which events Claude gets)

every air (wearer or other; the 3080's "another rider is airborne" candidates, `source:
"local"` rows from `other_air_rows`, go first and always, whatever their local number);
every fall / crash whose local P(yes) is in 0.2-0.9 or where
the local check and telemetry disagree; every event a proposed cut uses; the top 3 hero
shots, always. At most `CALLS_PER_RUN` (40) per first pass, in that priority order.
Each routed row carries `route_reasons` (words). Without a local stack the falls /
crashes go to Claude on the same priority list.

## Fields K5UI shows (on every event row and every index claim)

Event rows (`GET /api/verify` -> `events[]`, the planner's felt events) and, for old index
claims, the moment dicts inside `visual.moments[]` / `events[]` of the project payload:

    event_id      "CLIP_12:air:46.62"  stable id (stem:kind:start)
    kind, who     what it is and "wearer" | "other" (an air by another rider counts)
    start, end, t seconds in the clip
    certainty     how sure, in one word:
                    "claude"      Claude's strip check confirmed it (or retimed it)
                    "local"       the local model settled it (outside the 0.2-0.9 band)
                    "telemetry"   the sensor says so, nothing looked at the frames
                    "unchecked"   Local-only mode: nobody checked it
                    "unsure"      Claude was not sure (low confidence): shown, not asserted
                    "rejected"    Claude looked and it is not there: kept, not asserted
    evidence      ["telemetry", "local", "claude"] - who looked, in order
    asserted      bool - may the planner / board state it as fact. True for claude
                  confirm/retime and for telemetry/local-settled events; False for
                  unsure, rejected, unsupported - EXCEPT a sensor-proven air (below)
    sensor_proven true on an air whose own numbers prove the camera left the snow: free
                  fall >= 0.25 s at |a| <= 0.15 g, then a landing over 2.5 g, no handling.
                  When Claude rejected or was unsure about such an air the row reads
                  certainty "unsure", asserted TRUE ("felt"), and carries
                  `overruled` (words: "Claude rejected it, but the sensor read 0.30 s of
                  free fall and a 3.0 g landing: counted as felt"); verdict.raw_verdict is
                  still "reject" and verdict.why is what Claude saw. Show it as an air the
                  sensor proves and Claude could not see (the chip hover: `overruled`).
                  A camera Claude positively called thrown (kind camera-toss, confidence
                  above low) stays rejected. Other rejects (weaker numbers) stay rejected.
    verdict       null, or {verdict, kind, who, start, end, confidence, why, model, at,
                  raw_verdict}  - Claude's answer; `raw_verdict` is what it said before
                  low confidence turned it into "unsure"
    local         optional {p_yes} from the local model (LOCALGPU)
    route_reasons why this row went (or would go) to Claude
    facts         the numbers shown to the checker (telemetry text; a sensor-proven
                  air leads with "SENSOR PROOF: free fall X s, |a| down to Y g, landed Z g")
    source        "telemetry" | "local" - a "local" row is the 3080's own candidate (another
                  rider airborne: kind "air", who "other", evidence ["local"], certainty
                  "unchecked" and asserted False until Claude confirms; Local-only mode
                  asserts it, unchecked). See `other_air_rows` for the input contract.

Index claims (old Claude-index jump / fall / crash / inverted moments) also carry:

    claim_status  "supported" (telemetry overlaps, or Claude confirmed) | "unsupported"
                  (nothing backs it) | "rejected" (Claude looked, not there)
    supported_by  ["telemetry:air", "claude"]
    already_down  true on a fall claim backed only by a long "stopped" period that Claude
                  answered `lying-down` (the rider is down for the whole window, the going-
                  down is outside it): stays supported, certainty "unsure", never rejected
    weak_claim    (checkable rows only) a fall claim backed by nothing but a long stop: the
                  first pass sends these to Claude (`weak_claim_rows`)

and are *moved*, not deleted: `visual.moments[]` keeps only asserted ones; the rest are in
`visual.unsupported[]` with `claim_status`; the project payload's `events[]` keeps asserted
rows and `events_unsupported[]` the others. The planner never reads `unsupported[]`.

`GET /api/verify` -> {mode: mode_payload(), events: [...], unsupported: [...claims],
counts: {...}, claims_check: {n, usd, label: "Check N claims · price", charged,
backend}, job: {...}|null}. `POST /api/verify/claims {ids?: [claim ids]}` starts the
batch ("Check N claims"): a job whose state is in `GET /api/verify` `job`.
`POST /api/verify/events {ids?: [event ids]}` checks telemetry events the same way.
The first pass reports `plan.firstpass.verified` {mode, calls, usd, confirmed, rejected,
retimed, unsure, shots_flagged, flagged, contradicted, said}. `contradicted` lists every
proposed shot that rests on a moment a closer look contradicted (a Claude reject of its
event, or the index's own old moment that a close look found nothing for):
`[{shot (segment id), clip, in, out, event_id?, claimed ("an air"), found (what the closer
look said), text}]`. The shot carries `unverified: true`; the same rows are in the plan's
`checks` (Things to check, `kind: "contradicted"`, with `claimed` / `found`, and `shot` the
proposed shot's index) - this is what the shot chip's hover shows: what was claimed, what
the closer look found. A sensor-proven air Claude could not see is NOT contradicted.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import config, inference

# --------------------------------------------------------------------- constants

MODES = ("hybrid", "local", "claude")
DEFAULT_MODE = "hybrid"
MODE_LABELS = {"hybrid": "Hybrid", "local": "Local only", "claude": "Claude only"}

ROLE = config.ROLE_VERIFY
CALLS_PER_RUN = 40                  # a Killington first pass never spends more than this
BAND = (0.2, 0.9)                   # local P(yes): between these, Claude settles it
LOCAL_YES = 0.2                     # at or above: the local model says "yes, a fall"
WORKERS = 3                         # strip checks in flight at once

STRIP_COLS, STRIP_ROWS = 6, 4
STRIP_CELLS = STRIP_COLS * STRIP_ROWS
STRIP_WINDOW_S = 3.0                # 24 cells at 8 fps
STRIP_MAX_WINDOW_S = 6.0            # a long claim: 24 cells over up to 6 s (4 fps)
CELL_W = 261                        # 6 x 261 = 1566 px: inside the 1568 px long side
MAX_SIDE = 1568

# What one strip call costs when projected, from the R12 measurements: ~2.9k fresh +
# ~8.3k cached input tokens, 230-720 output (the CLI counts cached input at full rate
# in `projected_usd`, deliberately an over-estimate).
CALL_TOKENS = (11500, 650)

HOT_CLAIMS = {"jump", "fall", "crash"}              # events.HOT
_INVERTED = re.compile(r"\b(inverted|upside[- ]?down)\b", re.I)
CLAIM_FAMILY = {"jump": "air", "air": "air", "fall": "down", "crash": "down",
                "impact": "down", "speed_drop": "down", "stopped": "down",
                "camera-into-snow": "down", "inverted": "air"}
# telemetry kinds that can back a fall / crash claim
DOWN_TELEMETRY = {"impact", "speed_drop", "stopped"}
# The demotion pass is the rule, not an option. A test that exercises the visual pipeline
# with fabricated claims and no telemetry switches it off for itself (monkeypatch).
DEMOTE = True
SUPPORT_SLACK_S = 1.0               # a claim and a sensor event within this are the same seconds

VERDICTS = ("confirm", "reject", "retime", "unsure")
KINDS = ("air", "fall", "crash", "camera-into-snow", "camera-toss", "lying-down",
         "none", "other")

SYSTEM = (
    "You are a strict video-event checker for ski/snowboard POV footage (helmet-mounted "
    "GoPro). You are shown ONE image: a contact sheet of consecutive frames, 6 columns x "
    "4 rows, read left to right then top to bottom, each cell labelled with its clip "
    "time in seconds. Frames are levelled (horizon straightened using the camera's "
    "gravity sensor). Judge what is visible in the frames. "
    "A claim comes from one of two places. (1) An EARLIER LOOK at sparse frames (one "
    "every few seconds): it is often wrong, so reject it when these frames do not show "
    "the event. (2) TELEMETRY from the camera's sensors: a reliable clock for WHEN the "
    "camera felt something, but not for WHAT happened. A reading marked STRONG (true "
    "free fall: |a| near 0 g for a quarter second or more, then a hard landing) means "
    "the camera really was airborne at that moment, as a rider's jump or drop, or as a "
    "dropped, thrown or handled camera: your job is to tell which from the frames, "
    "confirming a rider's flight and rejecting a camera that was handled. A reading "
    "marked WEAK (|a| only 0.25-0.5 g) is just an unweighting a skier gets over a crest "
    "without leaving the snow: then the frames alone must show the flight, and "
    "continuous ordinary riding means reject. Whatever the numbers, never invent what "
    "is not in the frames. Cues for an air: skis or poles hanging or pointing up, the "
    "ground falling away or a crest passing, the horizon or pitch changing during the "
    "flight, then a jolt, spray or settling at landing - even when the take-off frame "
    "is not crisp (a 0.3 s air is only two or three cells). If the claimed event "
    "happened and something else follows it (a fall after the landing), still answer "
    "for the claimed event and mention the rest in `why`. "
    "Typical false claims: a person ahead of the camera mistaken for the wearer, "
    "gloves/poles/skis filling the frame, the wearer lying down on purpose, the camera "
    "being handled or tossed rather than a fall, ordinary riding over rough snow. "
    "Definitions: 'air' = anyone in frame is airborne, the wearer (their skis leave the "
    "snow, a jump or a drop) or ANOTHER rider (e.g. a skier flying over or ahead of the "
    "camera) - say which in `who`; 'fall' = a rider loses balance and goes down; "
    "'crash' = a hard fall or collision; 'camera-into-snow' = the camera hits the snow "
    "in a fall. A fall needs the going-down itself in these frames: the body, skis or "
    "poles tumbling, a hit on the snow, the camera ending at snow level. A view that "
    "tilts up, a person standing nearby, or a rider who is already down or lying on the "
    "snow for the whole window (the onset is not in the frames) is NOT a fall: answer "
    "lying-down (or none) and reject the fall claim. Use confidence low only when you truly cannot tell from these frames: "
    "then the answer is treated as unsure and nothing is asserted.")

SCHEMA = {
    "verdict": "confirm|reject|retime",
    "kind": "air|fall|crash|camera-into-snow|camera-toss|lying-down|none|other",
    "who": "wearer|other",
    "start": "<clip seconds or null>",
    "end": "<clip seconds or null>",
    "confidence": "high|medium|low",
    "why": "one or two sentences naming the frames/times you used",
}


# ---------------------------------------------------------------- providers (seams)
#
# The checker reads three things that other lanes own. Each has one function here, with a
# default that uses the lane's module when it is installed and a setter so a test (or the
# eval script) can supply its own.

def _default_telemetry(clip: str) -> list[dict]:
    try:
        from . import telemetry_events as te
    except ImportError:
        return []
    try:
        return list(te.telemetry_events(clip))
    except Exception:                                      # noqa: BLE001
        return []


def _default_levelling(clip: str, times: Sequence[float]) -> list[float]:
    """Degrees to rotate each frame so the horizon is level (0 when unknown)."""
    try:
        from . import telemetry_events as te
        roll = list(te.roll_at(clip, list(times)))
        sign = 1.0 if te.mount(clip) == "upright" else -1.0
        return [sign * float(r) for r in roll]
    except Exception:                                      # noqa: BLE001
        return [0.0] * len(times)


def _default_local(clip: str) -> list[dict]:
    """The 3080's checks for a clip (LOCALGPU's sidecar), [] when there is none."""
    try:
        from . import localvlm
        return list(localvlm.checks(clip))
    except Exception:                                      # noqa: BLE001
        return []


_LOCAL: Callable[[str], list[dict]] = _default_local
_TELEMETRY: Callable[[str], list[dict]] = _default_telemetry
_LEVELLING: Callable[[str, Sequence[float]], list[float]] = _default_levelling
_FRAMES: Callable[..., Any] | None = None       # None: localvlm if there, else ffmpeg


def set_providers(*, telemetry: Callable[[str], list[dict]] | None = None,
                  levelling: Callable[[str, Sequence[float]], list[float]] | None = None,
                  frames: Callable[..., Any] | None = None,
                  local: Callable[[str], list[dict]] | None = None) -> None:
    """Replace a provider; None puts the default back. Tests and the eval script use it."""
    global _TELEMETRY, _LEVELLING, _FRAMES, _LOCAL
    _LOCAL = local or _default_local
    _TELEMETRY = telemetry or _default_telemetry
    _LEVELLING = levelling or _default_levelling
    _FRAMES = frames


def telemetry_for(clip: str) -> list[dict]:
    return _TELEMETRY(clip)


def local_available() -> bool:
    """Whether a local model stack (GPU) is installed and usable here."""
    try:
        from . import localvlm
        return bool(localvlm.available().get("ok"))
    except Exception:                                      # noqa: BLE001
        return False


# ------------------------------------------------------------------- mode + storage

def state_path(work: Path, bin_name: str) -> Path:
    return Path(work) / "index" / f"{bin_name}.verify.json"


def load_state(path: Path) -> dict:
    """The bin's verify record: {mode, verdicts: {id: verdict}}. Never raises."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    verdicts = d.get("verdicts")
    return {"mode": d.get("mode") if d.get("mode") in MODES else DEFAULT_MODE,
            "verdicts": verdicts if isinstance(verdicts, dict) else {}}


_SAVE = threading.RLock()
_CACHE: dict = {}


def load_state_cached(path: Path) -> dict:
    """`load_state`, re-read only when the file changed (the payload builders call it once
    per clip)."""
    try:
        st = Path(path).stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    hit = _CACHE.get(str(path))
    if hit is not None and hit[0] == sig:
        return hit[1]
    d = load_state(path)
    _CACHE[str(path)] = (sig, d)
    return d


def merge_verdicts(path: Path, verdicts: dict) -> None:
    """Add verdicts to the stored record without disturbing anything else in it (the
    mode may have been changed while a batch ran)."""
    with _SAVE:
        cur = load_state(path)
        cur["verdicts"].update(verdicts)
    save_state(path, cur)


def save_state(path: Path, state: dict) -> None:
    path = Path(path)
    with _SAVE:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"version": 1, "mode": state.get("mode", DEFAULT_MODE),
                                   "verdicts": state.get("verdicts", {})}, indent=1),
                       encoding="utf-8")
        tmp.replace(path)


def set_mode(path: Path, mode: str) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {list(MODES)}")
    with _SAVE:
        st = load_state(path)
    st["mode"] = mode
    save_state(path, st)
    return st


def effective_mode(mode: str, local_ok: bool | None = None) -> str:
    """What runs. Hybrid with no local stack behaves like Claude only for the local half."""
    ok = local_available() if local_ok is None else local_ok
    return "claude" if mode == "hybrid" and not ok else mode


def mode_payload(mode: str, local_ok: bool | None = None) -> dict:
    ok = local_available() if local_ok is None else local_ok
    eff = effective_mode(mode, ok)
    if mode == "hybrid" and not ok:
        note = ("No local model is set up on this machine, so Hybrid runs as Claude only "
                "for the half the local model would have done: telemetry finds the moments "
                "and Claude checks the ones that matter.")
    elif mode == "hybrid":
        note = ("Telemetry finds the moments, the local model settles what it can, and "
                "Claude checks every air, the doubtful falls, and what the cut uses.")
    elif mode == "local":
        note = ("No Claude calls: moments come from telemetry and the local model, and are "
                "marked unchecked.")
    else:
        note = ("Claude only: telemetry finds the moments and Claude checks them; "
                "the local model is not used.")
    return {"mode": mode, "label": MODE_LABELS[mode], "modes": list(MODES),
            "default": DEFAULT_MODE, "local_available": ok, "effective": eff,
            "note": note}


# ------------------------------------------------------------------------ events

def stem(clip: str) -> str:
    return Path(str(clip)).stem


def event_id(e: dict) -> str:
    return f"{stem(e['clip'])}:{e.get('kind', '?')}:{float(e['start']):.2f}"


def claim_id(clip: str, kind: str, start: float) -> str:
    return f"{stem(clip)}:claim:{kind}:{float(start):.2f}"


def telemetry_events_for(clips: Iterable[str]) -> list[dict]:
    """All felt events of the given clips, each with its `clip`, an `event_id`, and the
    default fields a row always has."""
    out = []
    for clip in clips:
        rows = []
        for e in telemetry_for(clip):
            row = dict(e)
            row.setdefault("clip", clip)
            row.setdefault("who", "wearer")
            row.setdefault("t", (float(row["start"]) + float(row["end"])) / 2)
            row["event_id"] = event_id(row)
            rows.append(row)
        attach_local(rows)
        rows += other_air_rows(clip, rows)
        out += rows
    return out


def other_air_rows(clip: str, rows: Sequence[dict],
                   checks: Sequence[dict] | None = None) -> list[dict]:
    """Event rows for the 3080's "is anyone in frame airborne" candidates that name another
    rider (`who: other`) and are not the wearer's own sensor-felt air (Karl, 2026-11-08:
    other riders' airs count; verdict 16 punch 4).

    Contract with lane OTHERAIR (`localvlm.checks(clip)` items): a check whose
    `cand` = {kind: "air", who: "other", start, end, t} (also accepted: `who: "other"` or
    `q: "air_other"` on the check itself), with `p_yes` when the model scored it. Each
    becomes {kind: "air", who: "other", source: "local", start, end, t, local: {p_yes},
    event_id, text}. `route` sends every one of them to Claude, whatever its p_yes: the
    local model finds candidates, it never settles an air."""
    try:
        cs = list(checks) if checks is not None else list(_LOCAL(clip))
    except Exception:                                      # noqa: BLE001
        return []
    wearer = [r for r in rows if r.get("kind") == "air"]
    out: list[dict] = []
    for c in cs:
        cand = c.get("cand") or {}
        who = str(cand.get("who") or c.get("who") or "").lower()
        if cand.get("kind") != "air" or (who != "other" and c.get("q") != "air_other"):
            continue
        try:
            t = float(cand.get("t", c.get("t")))
            a = float(cand.get("start", t - 0.5))
            b = float(cand.get("end", t + 0.5))
        except (TypeError, ValueError):
            continue
        if any(_overlaps(a, b, float(w["start"]), float(w["end"]), 1.0) for w in wearer):
            continue                         # the wearer's own air: telemetry has it
        if any(abs(float(o["t"]) - t) < 1.0 for o in out):
            continue
        row = {"clip": clip, "kind": "air", "who": "other", "source": "local",
               "start": round(a, 2), "end": round(b, 2), "t": round(t, 2),
               "text": f"another rider airborne at about {t:.1f} s (the 3080's frame scan)"}
        row["event_id"] = event_id(row)
        if c.get("p_yes") is not None:
            row["local"] = {**local_of(c), "advisory": True}
        out.append(row)
    return out


LOCAL_MATCH_S = 0.5
_LOCAL_FAMILY = {"air": "air", "impact": "down", "speed_drop": "down", "stopped": "down",
                 "fall": "down", "crash": "down", "jump": "air"}


def find_local(clip: str, event: dict, checks: Sequence[dict]) -> dict | None:
    """The 3080's check of this same candidate: same kind family, `t` within 0.5 s."""
    fam = _LOCAL_FAMILY.get(str(event.get("kind")))
    t = float(event.get("t", event.get("start", 0.0)))
    best, gap = None, LOCAL_MATCH_S + 1e-9
    for c in checks:
        cand = c.get("cand") or {}
        if _LOCAL_FAMILY.get(str(cand.get("kind"))) != fam:
            continue
        d = abs(float(cand.get("t", c.get("t", 0.0))) - t)
        if d < gap:
            best, gap = c, d
    return best


def local_of(check: dict) -> dict:
    return {"p_yes": check.get("p_yes"), "verdict": check.get("verdict"),
            "model": check.get("model") or "", "frames": check.get("frames"),
            "threshold": check.get("threshold"), "question": check.get("q")}


def attach_local(rows: list[dict], checks: Sequence[dict] | None = None) -> None:
    """Set row["local"] = {p_yes, verdict, model, frames} on every row the 3080 checked. An
    air's is advisory only (R12: the local model cannot settle an air)."""
    if not rows:
        return
    checks = list(checks) if checks is not None else list(_LOCAL(rows[0]["clip"]))
    if not checks:
        return
    for r in rows:
        c = find_local(r["clip"], r, checks)
        if c is not None and c.get("p_yes") is not None:
            r["local"] = {**local_of(c), "advisory": r.get("kind") == "air",
                          "corroborated": corroborated(r, rows)}


CORROBORATE_S = 3.0


def corroborated(e: dict, rows: Sequence[dict]) -> bool:
    """Another, different kind of sensor event of the fall family within 3 s (a speed drop
    then a stop, a drop then a jolt): evidence the local model's "no" does not overrule."""
    if e.get("kind") not in DOWN_TELEMETRY:
        return False
    for o in rows:
        if o is e or o.get("kind") == e.get("kind") or o.get("kind") not in DOWN_TELEMETRY \
                or o.get("handling"):
            continue
        if _overlaps(float(e["start"]), float(e["end"]), float(o["start"]), float(o["end"]),
                     CORROBORATE_S):
            return True
    return False


def is_checkable(e: dict) -> bool:
    """Whether a checker should ever look at this telemetry event (the module's `check`
    flag; an air always)."""
    return bool(e.get("check")) or e.get("kind") == "air"


def local_p(e: dict) -> float | None:
    v = e.get("local")
    if isinstance(v, dict):
        v = v.get("p_yes")
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def local_settles(e: dict) -> bool | None:
    """What the 3080 settles for a fall-like event: False (not a fall: P(yes) under the
    threshold), True (P(yes) over 0.9), None (the 0.2-0.9 band, no check, or an air: the
    local model never settles an air)."""
    p = local_p(e)
    if p is None or e.get("kind") not in DOWN_TELEMETRY | {"fall", "crash"}:
        return None
    if p < BAND[0]:
        return None if (e.get("local") or {}).get("corroborated") else False
    if p > BAND[1]:
        return True
    return None


def _telemetry_says_down(e: dict) -> bool:
    return e.get("kind") in DOWN_TELEMETRY and bool(e.get("check")) and not e.get("handling")


def hero_ids(events: Sequence[dict], n: int = 3) -> list[str]:
    """The top shots by what the sensor measured: longest airs first (then landing g),
    then hardest impacts."""
    def weight(e: dict) -> tuple:
        if e.get("kind") == "air":
            return (2, float(e.get("air_s") or 0.0), float(e.get("landing_g") or 0.0))
        return (1, float(e.get("peak_g") or 0.0), 0.0)
    ranked = sorted((e for e in events if e.get("kind") in ("air", "impact")),
                    key=weight, reverse=True)
    return [e["event_id"] for e in ranked[:n]]


def route(events: Sequence[dict], *, mode: str, local_ok: bool, used: Iterable[str] = (),
          heroes: Iterable[str] | None = None, limit: int = CALLS_PER_RUN,
          verdicts: dict | None = None) -> list[dict]:
    """Which events go to Claude, in priority order, each with its `route_reasons`.

    Local only routes nothing. Priority: used by the cut, hero, air, then the doubtful
    falls / crashes. Events whose verdict is already stored are not routed again."""
    if mode == "local":
        return []
    used, verdicts = set(used), verdicts or {}
    hero = set(heroes) if heroes is not None else set(hero_ids(events))
    picked: list[tuple[int, int, dict]] = []
    for i, e in enumerate(events):
        eid = e["event_id"]
        if eid in verdicts:
            continue
        reasons, prio = [], 9
        if eid in used and is_checkable(e):
            reasons.append("the cut uses it")
            prio = min(prio, 0)
        if eid in hero:
            reasons.append("one of the top shots")
            prio = min(prio, 1)
        if e.get("weak_claim"):
            reasons.append("the board would state this fall on a long stop alone")
            prio = min(prio, 1)
        if e.get("kind") == "air" and e.get("who") == "other" and e.get("source") == "local":
            reasons.append("the 3080 saw another rider airborne: Claude decides")
            prio = min(prio, 1)
        elif e.get("kind") == "air":
            reasons.append("every air is checked")
            prio = min(prio, 2)
        elif _is_fall_like(e):
            p = local_p(e)
            if mode == "hybrid" and local_ok:
                if p is not None and BAND[0] <= p <= BAND[1]:
                    reasons.append(f"the local model is unsure ({p:.2f})")
                    prio = min(prio, 3)
                elif p is not None and p < BAND[0] and (e.get("local") or {}).get("corroborated"):
                    reasons.append("the local model says no but the sensor has two signals")
                    prio = min(prio, 3)
                elif p is not None and p > BAND[1] and not _telemetry_says_down(e):
                    reasons.append("the local model says fall and the sensor does not")
                    prio = min(prio, 3)
                elif p is None and is_checkable(e):
                    reasons.append("not yet looked at by the local model")
                    prio = min(prio, 3)
            elif is_checkable(e):
                reasons.append("no local model: Claude settles it"
                               if mode == "hybrid" else "Claude only")
                prio = min(prio, 4)
        if reasons:
            picked.append((prio, i, {**e, "route_reasons": reasons}))
    picked.sort(key=lambda x: (x[0], x[1]))
    return [r for _, _, r in picked[:max(0, int(limit))]]


def _is_fall_like(e: dict) -> bool:
    return e.get("kind") in DOWN_TELEMETRY or e.get("kind") in ("fall", "crash")


# ------------------------------------------------------------------------- price

def per_call_usd(model: str | None = None) -> float:
    m = model or config.model_for(ROLE)
    return config.projected_usd(m, *CALL_TOKENS)


def price(n_calls: int, noun: str = "moments") -> dict:
    """What `n_calls` strip checks cost, with the words for the button."""
    n = max(0, int(n_calls))
    if n == 1 and noun.endswith("s"):
        noun = noun[:-1]                      # "Check 1 claim", not "Check 1 claims"
    usd = round(n * per_call_usd(), 4)
    return {"n": n, "usd": usd, "per_call_usd": round(per_call_usd(), 4),
            "label": (f"Check {n} {noun}" if n else f"No {noun} to check"),
            "model": config.model_for(ROLE), **config.cost_terms()}


# ------------------------------------------------------------------ strip builder

@dataclass
class Strip:
    path: Path
    times: list[float]
    angles: list[float]
    size: tuple[int, int]
    source: str                      # "localvlm" | "ffmpeg"


def window_for(start: float, end: float, duration: float | None = None) -> tuple[float, float]:
    """(first cell time, window length) for a claim or event spanning start..end: 3 s
    centred on it, or up to 6 s for a long claim (the 24 cells stretch)."""
    span = max(0.0, float(end) - float(start))
    if span <= STRIP_WINDOW_S:
        w, c = STRIP_WINDOW_S, (float(start) + float(end)) / 2
    elif span <= STRIP_MAX_WINDOW_S:
        w, c = span, (float(start) + float(end)) / 2
    else:
        w, c = STRIP_MAX_WINDOW_S, float(start) + STRIP_MAX_WINDOW_S / 2
    t0 = max(0.0, c - w / 2)
    if duration is not None and t0 + w > duration:
        t0 = max(0.0, duration - w)
    return round(t0, 3), w


def _ffmpeg_frames(video: Path, times: Sequence[float], width: int) -> list:
    """Frames at `times` from the video, CPU ffmpeg. One call: seek, resample to the
    strip's rate, scale. ffmpeg applies a stream's rotation itself (autorotate), so a
    master with a -180 display matrix comes out upright; a proxy already is."""
    from PIL import Image
    if not times:
        return []
    dt = times[1] - times[0] if len(times) > 1 else 0.125
    with tempfile.TemporaryDirectory(prefix="rc-strip-") as tmp:
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{times[0]:.3f}", "-i", str(video),
               "-t", f"{dt * len(times) + dt:.3f}", "-vf", f"fps={1 / dt:.4f},scale={width}:-2",
               "-q:v", "3", "-frames:v", str(len(times)), f"{tmp}/f%03d.jpg"]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        out = []
        for k in range(len(times)):
            p = Path(tmp) / f"f{k + 1:03d}.jpg"
            out.append(Image.open(p).convert("RGB") if p.exists() else None)
        for im in out:
            if im is not None:
                im.load()
        return out


def get_frames(video: Path, times: Sequence[float], width: int = CELL_W) -> tuple[list, str]:
    """(PIL images or None per time, which extractor). LOCALGPU's extractor when its
    stack is up (NVDEC, sharpest-of-5), else CPU ffmpeg - one function, same output."""
    if _FRAMES is not None:
        return list(_FRAMES(video, times, width)), "custom"
    if local_available():
        try:
            from . import localvlm
            fn = getattr(localvlm, "extract_frames", None)
            if fn is not None:
                frames = list(fn(video, list(times), width=width))
                if len(frames) == len(times):
                    return frames, "localvlm"
        except Exception:                                  # noqa: BLE001
            pass
    return _ffmpeg_frames(Path(video), times, width), "ffmpeg"


def build_strip(video: Path, clip: str, start: float, end: float, out: Path, *,
                duration: float | None = None) -> Strip:
    """ONE image for one event: 24 levelled cells, each labelled with its clip time."""
    from PIL import Image, ImageDraw, ImageFont
    t0, w = window_for(start, end, duration)
    dt = w / STRIP_CELLS
    times = [round(t0 + k * dt, 3) for k in range(STRIP_CELLS)]
    frames, source = get_frames(video, times, CELL_W)
    angles = _LEVELLING(clip, times)
    cells = []
    for im, a in zip(frames, angles):
        if im is None:
            cells.append(None)
            continue
        if im.width != CELL_W:
            im = im.resize((CELL_W, max(2, round(im.height * CELL_W / im.width))))
        if abs(a) > 0.5 and abs(a) < 80:
            im = im.rotate(a, resample=Image.BICUBIC, fillcolor=(0, 0, 0))
        cells.append(im)
    ref = next((c for c in cells if c is not None), None)
    cw, ch = (ref.size if ref is not None else (CELL_W, 147))
    sheet = Image.new("RGB", (STRIP_COLS * cw, STRIP_ROWS * ch), (0, 0, 0))
    try:
        font = ImageFont.load_default(size=13)
    except TypeError:                                      # old Pillow: fixed small font
        font = ImageFont.load_default()
    d = ImageDraw.Draw(sheet)
    for k, im in enumerate(cells):
        x, y = (k % STRIP_COLS) * cw, (k // STRIP_COLS) * ch
        if im is not None:
            sheet.paste(im.resize((cw, ch)) if im.size != (cw, ch) else im, (x, y))
        d.rectangle([x, y, x + 62, y + 16], fill=(0, 0, 0))
        d.text((x + 3, y + 1), f"{times[k]:.2f}s", fill=(255, 255, 255), font=font)
    scale = MAX_SIDE / max(sheet.size)
    if scale < 1:
        sheet = sheet.resize((int(sheet.width * scale), int(sheet.height * scale)))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "JPEG", quality=88)
    return Strip(out, times, [round(a, 1) for a in angles], sheet.size, source)


# ------------------------------------------------------------------------- facts

_NO_FACTS = "no numbers: this is a claim from an earlier look at sparse frames."


STRONG_MIN_G = 0.15      # every real air on the Killington test set read 0.03-0.10 g
STRONG_LANDING_G = 2.5


def strength(e: dict) -> str:
    """STRONG / WEAK for a telemetry air, from its own numbers (R12: the six real airs
    read 0.03-0.10 g and landed at 2.5-7.7 g; a 0.25-0.5 g dip is an unweighting)."""
    if e.get("kind") != "air" or e.get("min_g") is None:
        return ""
    strong = (float(e["min_g"]) <= STRONG_MIN_G
              and float(e.get("landing_g") or 0.0) >= STRONG_LANDING_G)
    return ("STRONG (true free fall)" if strong
            else "WEAK (an unweighting or a bump; the frames must show the flight)")


SENSOR_AIR_S = 0.25         # free fall at least this long ...
SENSOR_LANDING_G = 2.5      # ... then a landing harder than this ...
                            # ... with |a| at or under STRONG_MIN_G in between: sensor-proven


def sensor_proven(e: dict) -> bool:
    """A telemetry air whose own numbers prove the camera left the snow: true free fall
    (min |a| <= 0.15 g) for >= 0.25 s, then a landing over 2.5 g, and not a handled camera.
    On Karl's set exactly one rejected air reads this way (CLIP_05 17.97: 0.30 s, 0.03 g,
    3.0 g, a labelled high-confidence air that Claude called flat terrain); the six that
    Claude rightly rejected are all WEAK (0.16-0.21 g). A small air over a roll is
    invisible in a 3 s strip, so Claude's "no crest" cannot outvote the sensor."""
    if e.get("kind") != "air" or e.get("handling") or e.get("source") == "local":
        return False
    try:
        return (float(e.get("air_s") or 0.0) >= SENSOR_AIR_S - 1e-9
                and float(e["min_g"]) <= STRONG_MIN_G
                and float(e.get("landing_g") or 0.0) > SENSOR_LANDING_G)
    except (KeyError, TypeError, ValueError):
        return False


def overruled(e: dict, v: dict | None) -> bool:
    """Claude did not confirm a sensor-proven air (rejected it, or was unsure): the sensor
    keeps it. Not when Claude positively named a handled / tossed camera with more than
    low confidence, which is what the sensor cannot tell from a jump."""
    if not v or v.get("verdict") not in ("reject", "unsure") or not sensor_proven(e):
        return False
    raw = v.get("raw_verdict") or v.get("verdict")
    if raw == "reject" and v.get("kind") == "camera-toss" and v.get("confidence") != "low":
        return False
    return raw in ("reject", "unsure", "confirm")


def facts_text(e: dict, neighbours: Sequence[dict] = ()) -> str:
    """The event's facts for the prompt: the telemetry numbers when there are any, what the
    earlier look said when it came from the old index, the local model's number when
    present, and the other sensor events inside the strip."""
    lines = []
    src = e.get("source")
    if sensor_proven(e):
        lines.append(
            f"SENSOR PROOF (read this first): the camera was in true free fall for "
            f"{float(e['air_s']):.2f} s (|a| down to {float(e['min_g']):.2f} g, a rider on the "
            f"snow never reads under 0.15 g) and then landed at {float(e['landing_g']):.1f} g. "
            "Something left the ground. A small air over a roll or a drop-off can look like "
            "continuous terrain in 3 s of frames: reject only if the frames positively show the "
            "camera handled, thrown or lying still.")
    if src == "telemetry" or e.get("kind") in ("air", "impact", "speed_drop", "stopped"):
        bits = []
        if e.get("text"):
            bits.append(str(e["text"]))
        for k, label in (("air_s", "free-fall seconds"), ("min_g", "minimum |a| in g"),
                         ("landing_g", "landing peak g"), ("peak_g", "impact peak g"),
                         ("from_ms", "speed before m/s"), ("to_ms", "speed after m/s")):
            if e.get(k) is not None:
                bits.append(f"{label} {e[k]}")
        if e.get("handling"):
            bits.append("no speed change around it (often a hand on the camera)")
        lines.append("Telemetry says: " + "; ".join(bits) + ".")
        st = strength(e)
        if st:
            lines.append(f"Reading strength: {st}.")
    else:
        lines.append("Earlier look says" + (f" '{e['what']}'" if e.get("what") else "") + ": "
                     + _NO_FACTS)
    p = local_p(e)
    if p is not None:
        lines.append(f"A small local model, looking at the same frames, gave P(yes)={p:.2f}.")
    if e.get("window_speed"):
        lines.append("GPS ground speed m/s around the window (about 1 Hz): "
                     f"{e['window_speed']}.")
    near = [n for n in neighbours
            if n.get("event_id") != e.get("event_id") and n.get("text")]
    if near:
        lines.append("Other sensor events in this window: "
                     + "; ".join(str(n["text"]) for n in near[:6]) + ".")
    return "\n".join(lines)


def window_speed(clip: str, t0: float, t1: float) -> list[float]:
    """GPS ground speed (m/s) in and just around the strip's window, when the telemetry
    module has it; [] otherwise."""
    try:
        from . import telemetry_events as te
        return [round(float(v), 1) for t, v in te.speed_series(clip)
                if t0 - 1.0 <= float(t) <= t1 + 1.0]
    except Exception:                                      # noqa: BLE001
        return []


def build_prompt(e: dict, strip: Strip, neighbours: Sequence[dict] = ()) -> str:
    kind = e.get("kind", "event")
    claimed = {"impact": "fall or crash (a hard impact)", "speed_drop": "fall (a sudden stop)",
               "stopped": "stop", "jump": "air (a jump)"}.get(kind, kind)
    t0, t1 = strip.times[0], strip.times[-1]
    dt = strip.times[1] - strip.times[0]
    return (f"Window: clip {stem(e['clip'])}, times {t0:.2f} to {t1:.2f} s "
            f"(24 cells, one every {dt:.3f} s, labelled). Candidate event under review: "
            f"a '{claimed}' at about {float(e['start']):.2f}-{float(e['end']):.2f} s.\n"
            + facts_text({**e, "window_speed": e.get("window_speed")
                          or window_speed(e["clip"], strip.times[0], strip.times[-1])},
                         neighbours) + "\n"
            + "Camera roll (degrees the frames were levelled by), per cell in order: "
            + str(strip.angles) + "\n"
            "Question: is this candidate a real event of that kind in these frames? Decide "
            "confirm (it is real as stated), reject (not real), or retime (a real event "
            "happens but at a different time or of a different kind - give the true "
            "kind/times). Say in `who` whether the wearer or another rider is in the event.")


# ------------------------------------------------------------------------ verdicts

def validate(obj: Any) -> dict:
    """The model's JSON as a clean verdict, or ValueError (the bounded re-ask)."""
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    v = str(obj.get("verdict", "")).strip().lower()
    if v not in ("confirm", "reject", "retime"):
        raise ValueError(f"verdict must be confirm, reject or retime, not {v!r}")
    kind = str(obj.get("kind", "other")).strip().lower()
    if kind not in KINDS:
        kind = "other"
    who = str(obj.get("who", "wearer")).strip().lower()
    who = who if who in ("wearer", "other") else "wearer"
    conf = str(obj.get("confidence", "low")).strip().lower()
    conf = conf if conf in ("high", "medium", "low") else "low"

    def num(x):
        try:
            return None if x is None else round(float(x), 2)
        except (TypeError, ValueError):
            return None
    return {"verdict": v, "kind": kind, "who": who, "start": num(obj.get("start")),
            "end": num(obj.get("end")), "confidence": conf,
            "why": str(obj.get("why", ""))[:500]}


def settle(v: dict, e: dict | None = None) -> dict:
    """Low confidence is 'unsure' - shown to Karl, never asserted (R12). Given the event it
    was about: a sensor-proven air (`sensor_proven`) that Claude rejected becomes 'unsure'
    too - shown, and still asserted as felt (`annotate_event`), `overruled: true`, with
    `raw_verdict` still saying what Claude said."""
    v = dict(v)
    v["raw_verdict"] = v["verdict"]
    if v.get("confidence") == "low":
        v["verdict"] = "unsure"
    if e is not None and v["raw_verdict"] == "reject" and overruled(e, {**v, "verdict": "reject"}):
        v["verdict"], v["overruled"] = "unsure", True
    return v


def check_event(e: dict, video: Path, workdir: Path, *, neighbours: Sequence[dict] = (),
                duration: float | None = None) -> dict:
    """One Claude strip check of one event or claim. Raises InferenceError. The strip is
    kept under `workdir` (the board can show what Claude saw)."""
    strip = build_strip(video, e["clip"], float(e["start"]), float(e["end"]),
                        Path(workdir) / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', e['event_id'])}.jpg",
                        duration=duration)
    res = inference.complete(build_prompt(e, strip, neighbours), role=ROLE,
                             images=[strip.path], schema=SCHEMA, system=SYSTEM,
                             validate=validate, isolated=True)
    v = settle(res.content, e)
    v.update(model=res.model, at=round(time.time(), 1), strip=strip.path.name,
             strip_source=strip.source, projected_usd=res.projected_usd,
             window=[strip.times[0], strip.times[-1]])
    return v


def run_checks(rows: Sequence[dict], *, video_of: Callable[[str], Path], workdir: Path,
               state: dict, save: Callable[[dict], None] | None = None,
               duration_of: Callable[[str], float | None] = lambda c: None,
               neighbours_of: Callable[[dict], Sequence[dict]] = lambda e: (),
               on_done: Callable[[dict, dict | None, str | None], None] | None = None,
               cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Check each row (a telemetry event or an index claim, with an `event_id`), store
    each verdict in `state["verdicts"]` as it lands, and return the run's summary. A
    failed call is recorded in the summary and costs nothing more; the batch goes on."""
    summary = {"calls": 0, "usd": 0.0, "errors": [], "stopped": False,
               "confirmed": 0, "rejected": 0, "retimed": 0, "unsure": 0}

    def one(e: dict):
        if cancelled():
            return e, None, "cancelled"
        try:
            return e, check_event(e, video_of(e["clip"]), workdir,
                                  neighbours=neighbours_of(e),
                                  duration=duration_of(e["clip"])), None
        except inference.BudgetExceeded as exc:
            return e, None, f"budget: {exc}"
        except (inference.InferenceError, OSError, subprocess.SubprocessError,
                ValueError) as exc:
            return e, None, str(exc)[:300]

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for e, v, err in pool.map(one, rows):
            if err == "cancelled":
                summary["stopped"] = True
            elif err:
                summary["errors"].append({"event_id": e["event_id"], "error": err})
            elif v is not None:
                state["verdicts"][e["event_id"]] = v
                summary["calls"] += 1
                summary["usd"] = round(summary["usd"] + float(v.get("projected_usd") or 0), 4)
                key = {"confirm": "confirmed", "reject": "rejected", "retime": "retimed",
                       "unsure": "unsure"}[v["verdict"]]
                summary[key] += 1
                if save is not None:
                    save(state)
            if on_done is not None:
                on_done(e, v, err)
    return summary


# ----------------------------------------------------------- annotating events/claims

def annotate_event(e: dict, state: dict, *, mode: str, local_ok: bool) -> dict:
    """The telemetry event as a row with its certainty (fields in the module docstring)."""
    row = dict(e)
    eid = row.get("event_id") or event_id(row)
    row["event_id"] = eid
    v = state.get("verdicts", {}).get(eid)
    local_src = row.get("source") == "local"
    evidence = ["local"] if local_src else ["telemetry"]
    p = local_p(row)
    if p is not None and not local_src:
        evidence.append("local")
    row["verdict"] = v
    if v is not None:
        evidence.append("claude")
        verdict = v["verdict"]
        row["certainty"] = {"confirm": "claude", "retime": "claude", "unsure": "unsure",
                            "reject": "rejected"}[verdict]
        row["asserted"] = verdict in ("confirm", "retime")
        if overruled(row, v):
            # the sensor proves free fall and Claude could not see the flight: shown as
            # unsure, still felt, never demoted (verdict 16, punch 3)
            row["certainty"], row["asserted"], row["sensor_proven"] = "unsure", True, True
            row["overruled"] = ("Claude " + ("rejected" if (v.get("raw_verdict") or verdict)
                                             == "reject" else "was unsure about") +
                                f" it, but the sensor read {float(row['air_s']):.2f} s of free "
                                f"fall and a {float(row['landing_g']):.1f} g landing: "
                                "counted as felt")
        if verdict == "retime" and v.get("kind") not in (None, "none", "other"):
            row["kind_checked"] = v["kind"]
        if v.get("who") in ("wearer", "other"):
            row["who"] = v["who"]
    elif local_src:
        # the 3080's own candidate (another rider's air): only Claude can state it as fact;
        # Local only mode has no Claude, so it is stated, marked unchecked
        row["certainty"], row["asserted"] = "unchecked", mode == "local"
    elif local_settles(row) is not None:
        row["certainty"], row["asserted"] = "local", bool(local_settles(row))
    elif mode == "local":
        row["certainty"], row["asserted"] = "unchecked", True
    else:
        row["certainty"], row["asserted"] = "telemetry", True
    row["evidence"] = evidence
    return row


MOMENT_MATCH_S = 0.5


def match_event(m: dict, rows: Sequence[dict]) -> dict | None:
    """The verify row a coverage moment stands for: the same `event_id`, else the same kind
    family with the two spans within 0.5 s of each other (nearest wins)."""
    eid = m.get("event_id")
    if eid:
        for r in rows:
            if r.get("event_id") == eid:
                return r
    fam = CLAIM_FAMILY.get(str(m.get("kind")))
    if fam is None:
        return None
    try:
        a0, a1 = float(m["start"]), float(m.get("end", m["start"]))
    except (KeyError, TypeError, ValueError):
        return None
    best, gap = None, MOMENT_MATCH_S + 1e-9
    for r in rows:
        if CLAIM_FAMILY.get(str(r.get("kind"))) != fam:
            continue
        try:
            b0, b1 = float(r["start"]), float(r.get("end", r["start"]))
        except (KeyError, TypeError, ValueError):
            continue
        d = max(0.0, max(a0, b0) - min(a1, b1))
        if d < gap:
            best, gap = r, d
    return best


def certify_moments(moments: Sequence[dict], rows: Sequence[dict]) -> list[dict]:
    """Coverage moments carrying VERIFY's verdict where an annotated event row exists for
    that moment: `certainty` (claude | local | telemetry | unsure | rejected | unchecked),
    `verdict` {verdict, by}, `asserted`. A moment the 3080 settled as not-a-fall reads as
    rejected by the 3080. Pure; moments with no event row come back unchanged."""
    out = []
    for m in moments:
        r = match_event(m, rows) if rows else None
        if r is None or not r.get("certainty"):
            out.append(m)
            continue
        c = r["certainty"]
        n = {**m, "certainty": c, "asserted": r.get("asserted")}
        v = r.get("verdict")
        if v:
            n["verdict"] = {"verdict": v.get("verdict"), "by": "claude"}
        elif c == "local":
            n["verdict"] = {"verdict": "confirm" if r.get("asserted") else "reject",
                            "by": "local"}
        out.append(n)
    return out


def _overlaps(a0: float, a1: float, b0: float, b1: float, slack: float) -> bool:
    return a0 - slack <= b1 and b0 - slack <= a1


def is_claim(m: dict) -> bool:
    """An old index claim the checker must back: a jump / fall / crash, or anything the
    index worded as inverted / upside-down. A deep look's own events are not: the editor
    paid for that read of the seconds (roughcut.deep) and it is Claude's checked account."""
    if m.get("deep") or m.get("source") == "deep look":
        return False
    return (str(m.get("kind", "")).lower() in HOT_CLAIMS
            or bool(_INVERTED.search(str(m.get("what", "")))))


def claim_family(m: dict) -> str:
    k = str(m.get("kind", "")).lower()
    if k in CLAIM_FAMILY:
        return CLAIM_FAMILY[k]
    return "air" if _INVERTED.search(str(m.get("what", ""))) else "down"


def _down_supporters(m: dict, tel: Sequence[dict]) -> list[dict]:
    """Telemetry events of the fall family that overlap the claim (a second of slack)."""
    try:
        a, b = float(m["start"]), float(m.get("end", m["start"]))
    except (KeyError, TypeError, ValueError):
        return []
    return [t for t in tel if t.get("kind") in DOWN_TELEMETRY and not t.get("handling")
            and _overlaps(a, b, float(t["start"]), float(t["end"]), SUPPORT_SLACK_S)]


def weakly_backed(m: dict, tel: Sequence[dict]) -> bool:
    """A fall / crash claim whose only sensor backing is a long `stopped` period (events the
    telemetry module itself does not offer for a check)."""
    sup = _down_supporters(m, tel)
    return bool(sup) and all(t.get("check") is False for t in sup)


def claim_support(m: dict, clip: str, tel: Sequence[dict], state: dict) -> dict:
    """Is this claim backed? -> {claim_status, supported_by}. Backed = a sensor event of
    the same family within a second, which the checker has not rejected, or Claude's own
    confirm / retime over the claim. A claim Claude rejected is `rejected`."""
    verdicts = state.get("verdicts", {})
    cid = claim_id(clip, str(m.get("kind", "")), float(m["start"]))
    v = verdicts.get(cid)
    if v is not None:
        if v["verdict"] == "reject" and v.get("kind") == "lying-down" \
                and claim_family(m) == "down" and weakly_backed(m, tel):
            # The index said the rider is down and a stop backs it; Claude looked and saw the
            # rider already down for the whole window, the going-down itself outside it. The
            # state is real, only "fall" as an event is not in these frames: shown and stated,
            # marked unsure, never rejected (Karl's set: CLIP_01 83-111 "skier down, buried")
            return {"claim_status": "supported", "supported_by": ["telemetry:stopped", "claude"],
                    "claim_id": cid, "verdict": v, "already_down": True}
        if v["verdict"] == "reject":
            return {"claim_status": "rejected", "supported_by": [], "claim_id": cid,
                    "verdict": v}
        if v["verdict"] in ("confirm", "retime"):
            return {"claim_status": "supported", "supported_by": ["claude"],
                    "claim_id": cid, "verdict": v}
    fam = claim_family(m)
    by = []
    for t in tel:
        tk = t.get("kind")
        tfam = "air" if tk == "air" else "down" if tk in DOWN_TELEMETRY else None
        if tfam != fam or t.get("handling"):
            continue
        if not _overlaps(float(m["start"]), float(m["end"]), float(t["start"]),
                         float(t["end"]), SUPPORT_SLACK_S):
            continue
        tv = verdicts.get(t.get("event_id") or event_id({**t, "clip": clip}))
        if tv is not None and tv["verdict"] in ("reject",) \
                and not overruled({**t, "clip": clip}, tv):
            continue
        by.append(f"telemetry:{tk}")
    if by:
        return {"claim_status": "supported", "supported_by": by, "claim_id": cid,
                "verdict": v}
    return {"claim_status": "unsupported", "supported_by": [], "claim_id": cid, "verdict": v}


def _checker_confirmed(state: dict, clip: str, e: dict) -> bool:
    v = state.get("verdicts", {}).get(e.get("event_id") or event_id({**e, "clip": clip}))
    return bool(v and v["verdict"] in ("confirm", "retime"))


def demote(moments: Sequence[dict], clip: str, state: dict,
           tel: Sequence[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Split one clip's index moments into (asserted, not asserted). Non-claims (talk,
    scenery, reaction...) pass through untouched. Claims keep their `claim_status`; the
    others are returned in the second list, never dropped."""
    if not DEMOTE:
        return list(moments), []
    tel = list(tel) if tel is not None else telemetry_events_for([clip])
    asserted, held = [], []
    for m in moments:
        if m.get("source") == "telemetry" and isinstance(m.get("felt"), dict):
            f = {**m["felt"], "clip": clip}
            same = next((t for t in tel if t.get("kind") == f.get("kind")
                         and abs(float(t["start"]) - float(f["start"])) < 0.06), None)
            if same is not None:
                f = same
            if f.get("kind") != "air" and local_settles(f) is False \
                    and not _checker_confirmed(state, clip, f):
                held.append({**m, "claim_status": "settled-local", "supported_by": [],
                             "certainty": "local", "asserted": False, "local": f.get("local")})
                continue
        if not is_claim(m):
            asserted.append(m)
            continue
        s = claim_support(m, clip, tel, state)
        row = {**m, "claim_status": s["claim_status"], "supported_by": s["supported_by"],
               "claim_id": s["claim_id"]}
        if s.get("verdict") is not None:
            row["verdict"] = s["verdict"]
        row["certainty"] = {"supported": "telemetry" if s["supported_by"] != ["claude"]
                            else "claude", "unsupported": "unsupported",
                            "rejected": "rejected"}[s["claim_status"]]
        row["asserted"] = s["claim_status"] == "supported"
        if s.get("already_down"):
            row["certainty"] = "unsure"
            row["already_down"] = True
        (asserted if row["asserted"] else held).append(row)
    return asserted, held


def demote_ranked(rows: Sequence[dict], state: dict,
                  tel_of: Callable[[str], Sequence[dict]] | None = None
                  ) -> tuple[list[dict], list[dict]]:
    """`demote` over the bin's ranked events (rows from different clips, in rank order)."""
    cache: dict[str, list[dict]] = {}

    def tel(clip: str) -> list[dict]:
        if clip not in cache:
            cache[clip] = list(tel_of(clip)) if tel_of else telemetry_events_for([clip])
        return cache[clip]
    asserted, held = [], []
    for r in rows:
        a, h = demote([r], r.get("clip", ""), state, tel(r.get("clip", "")))
        asserted += a
        held += h
    return asserted, held


def claim_rows(clips_moments: dict[str, Sequence[dict]], state: dict,
               tel_of: Callable[[str], Sequence[dict]] | None = None) -> list[dict]:
    """Every claim that is `unsupported` and not yet checked, as checkable rows, for the
    "Check N claims" batch."""
    out = []
    for clip, moments in clips_moments.items():
        tel = list(tel_of(clip)) if tel_of else telemetry_events_for([clip])
        for m in moments:
            if not is_claim(m):
                continue
            s = claim_support(m, clip, tel, state)
            if s["claim_status"] != "unsupported" or s["verdict"] is not None:
                continue
            kind = "air" if claim_family(m) == "air" else str(m.get("kind") or "fall")
            out.append({"clip": clip, "kind": kind, "start": float(m["start"]),
                        "end": float(m["end"]), "what": m.get("what", ""), "who": "wearer",
                        "source": "index", "event_id": s["claim_id"],
                        "claim_kind": m.get("kind")})
    return out


def weak_claim_rows(ranked: Sequence[dict], state: dict,
                    tel_of: Callable[[str], Sequence[dict]] | None = None) -> list[dict]:
    """Index fall / crash claims the board would state as fact on the strength of nothing but
    a long `stopped` telemetry period (or a handled-camera impact): the sensor module itself
    does not offer those for a check, a stop cannot date a fall, and a standing wearer is
    "stopped" for a minute. Unchecked ones come back as checkable claim rows (`weak_claim`),
    so the first pass sends them to Claude: on Karl's set three labelled non-events (CLIP_11
    252-260, CLIP_06 204-220, CLIP_12 1.4-1.6) were stated as falls on a stop alone, and
    one real fall (CLIP_01 103-109) rests on the same kind of stop - only a look tells them
    apart. `ranked` are ranked event rows or index moments with `clip`, `kind`, `start`,
    `end`."""
    out, seen = [], set()
    cache: dict[str, list[dict]] = {}
    for m in ranked:
        clip = m.get("clip") or ""
        if not clip or not is_claim(m) or claim_family(m) != "down" \
                or m.get("source") == "telemetry":
            continue
        try:
            a, b = float(m["start"]), float(m["end"])
        except (KeyError, TypeError, ValueError):
            continue
        cid = claim_id(clip, str(m.get("kind", "")), a)
        if cid in state.get("verdicts", {}) or cid in seen:
            continue
        if clip not in cache:
            cache[clip] = list(tel_of(clip)) if tel_of else telemetry_events_for([clip])
        if not weakly_backed(m, cache[clip]):
            continue                     # backed by a real candidate (or by nothing: unsupported)
        seen.add(cid)
        out.append({"clip": clip, "kind": str(m.get("kind") or "fall"), "start": a, "end": b,
                    "t": (a + b) / 2, "what": m.get("what", ""), "who": "wearer",
                    "source": "index", "event_id": cid, "claim_kind": m.get("kind"),
                    "weak_claim": True})
    return out


# ------------------------------------------------------------- first-pass hook

def segments_using(segments: Sequence[dict], events: Sequence[dict]) -> dict[str, list[dict]]:
    """event_id -> the cut's shots that cover it (same clip, overlapping seconds)."""
    out: dict[str, list[dict]] = {}
    for e in events:
        for s in segments:
            if s.get("clip") == e["clip"] and _overlaps(
                    float(s["in"]), float(s["out"]), float(e["start"]), float(e["end"]), 0.0):
                out.setdefault(e["event_id"], []).append(s)
    return out


def _stem_clock(clip: str, a: float, b: float) -> str:
    def clock(t: float) -> str:
        return f"{int(t // 60)}:{t % 60:04.1f}"
    return f"{stem(clip)} {clock(a)}–{clock(b)}"


def _short(text: str, limit: int = 220) -> str:
    """The closer look's words for a Things-to-check line: whole sentences up to `limit`
    characters (the full text stays in `found`, which the chip hover shows)."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    out = ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if out and len(out) + len(sent) + 1 > limit:
            break
        out = f"{out} {sent}".strip()
    return out if out and len(out) <= limit + 60 else text[:limit].rstrip() + "…"


def _claimed(e: dict) -> str:
    """What an event row claimed, in words: "an air (another rider)" / the sensor's text."""
    kind = {"impact": "fall or crash", "speed_drop": "fall", "air": "air"}.get(
        str(e.get("kind")), str(e.get("kind") or "event"))
    who = " by another rider" if e.get("who") == "other" else ""
    return f"{'an' if kind[0] in 'aeiou' else 'a'} {kind}{who}"


def apply_to_plan(plan: dict, events: Sequence[dict], state: dict, *, locked: Sequence[str] = (),
                  durations: Callable[[str], float | None] = lambda c: None,
                  contradicted_by: Callable[[dict], str | None] | None = None) -> dict:
    """Re-time the proposed cut from the verdicts and say which shots rest on a moment a
    closer look contradicted.

    - A shot covering an event Claude retimed is widened to hold the true moment (never
      narrowed; a locked shot is the editor's and is left alone).
    - A shot all of whose checked events Claude rejected (a sensor-proven air Claude could
      not see is `unsure`, not a rejection: `overruled`) is flagged `unverified`.
    - `contradicted_by(segment)` is the caller's hook for the index's own old moments (a
      jump the close look called flat snow): it returns the words for what contradicted the
      shot's moment, or None. A shot it names is flagged too.

    The cut is not narrowed or reordered here (the editor may have wanted the shot for its
    words); every flagged shot is **listed**: `contradicted` [{shot, clip, in, out, event_id?,
    claimed, found, text}] - `revise.plan_checks` turns each into a "Things to check" line
    that says what was claimed and what the closer look found, and the shot's chip hover
    reads `claimed` / `found`. Returns {"flagged": [shot ids], "retimed": n, "contradicted":
    [...]}."""
    used = segments_using(plan.get("segments") or [], events)
    by_id = {e["event_id"]: e for e in events}
    flagged, retimed, listed = [], 0, []
    locked = set(locked)
    by_seg: dict[int, list[str]] = {}
    for eid, segs in used.items():
        for s in segs:
            by_seg.setdefault(id(s), []).append(eid)
    for s in plan.get("segments") or []:
        eids = by_seg.get(id(s), [])
        vs = [(eid, state["verdicts"].get(eid)) for eid in eids]
        if eids and s.get("id") not in locked:
            for eid, v in vs:
                if v and v["verdict"] == "retime" and v.get("start") is not None:
                    a = float(v["start"]) - 0.3
                    b = float(v.get("end") if v.get("end") is not None else v["start"]) + 0.3
                    dur = durations(s["clip"])
                    lo, hi = max(0.0, min(float(s["in"]), a)), max(float(s["out"]), b)
                    if dur is not None:
                        hi = min(hi, dur)
                    if (lo, hi) != (float(s["in"]), float(s["out"])):
                        s["in"], s["out"] = round(lo, 2), round(hi, 2)
                        retimed += 1
        checked = [(eid, v) for eid, v in vs if v]
        rejected = [(eid, v) for eid, v in checked
                    if v["verdict"] == "reject" and not overruled(by_id.get(eid, {}), v)]
        found = None
        if checked and len(rejected) == len(checked) == len(vs):
            eid, v = rejected[0]
            e = by_id.get(eid, {})
            claimed = _claimed(e)
            found = {"event_id": eid, "claimed": claimed,
                     "found": str(v.get("why") or "a closer look did not find it").strip()}
        elif contradicted_by is not None:
            why = contradicted_by(s)
            if why:
                found = {"claimed": "a moment this shot rests on", "found": why}
        if found is None:
            continue
        s["unverified"] = True
        sid = s.get("id") or f"{s['clip']} {s['in']}"
        flagged.append(sid)
        where = _stem_clock(s["clip"], float(s["in"]), float(s["out"]))
        listed.append({"shot": s.get("id"), "clip": s["clip"], "in": s["in"], "out": s["out"],
                       **found,
                       "text": f"{where} rests on {found['claimed']} that a closer look "
                               f"contradicted: {_short(found['found'])}"})
    return {"flagged": flagged, "retimed": retimed, "contradicted": listed}


def verified_sentence(summary: dict, mode: str, flagged: int, retimed: int,
                      skipped: str = "") -> str:
    if skipped:
        return skipped
    n = summary.get("calls", 0)
    if not n:
        return "No moments were checked with Claude."
    parts = [f"{summary.get('confirmed', 0)} confirmed", f"{summary.get('rejected', 0)} rejected"]
    if summary.get("retimed"):
        parts.append(f"{summary['retimed']} corrected")
    if summary.get("unsure"):
        parts.append(f"{summary['unsure']} unsure")
    s = f"Claude checked {n} moment{'s' if n != 1 else ''} the cut uses ({', '.join(parts)})."
    if retimed:
        s += f" {retimed} shot{'s' if retimed != 1 else ''} widened to hold the true moment."
    if flagged:
        s += (f" {flagged} shot{'s' if flagged != 1 else ''} rest{'s' if flagged == 1 else ''} "
              "only on a moment Claude could not find.")
    return s


def verify_cut(plan: dict, clips: Sequence[str], state: dict, *, mode: str,
               video_of: Callable[[str], Path], workdir: Path,
               save: Callable[[dict], None] | None = None, locked: Sequence[str] = (),
               duration_of: Callable[[str], float | None] = lambda c: None,
               cancelled: Callable[[], bool] = lambda: False,
               limit: int = CALLS_PER_RUN, local_ok: bool | None = None,
               contradicted_by: Callable[[dict], str | None] | None = None,
               claims: Sequence[dict] = ()) -> dict:
    """The first-pass step after the cut is proposed: route, check the events the cut
    uses (plus heroes and airs, within `limit`), re-time the shots from the verdicts, and
    list the shots that rest on a contradicted moment (`apply_to_plan`; `contradicted_by`
    covers the index's own old moments). Mutates `plan["segments"]` in place; returns the
    `verified` record, whose `contradicted` is the list of shots to check. `claims` are
    `weak_claim_rows` (index falls resting on a stop alone), checked within the same limit."""
    ok = local_available() if local_ok is None else local_ok
    eff = effective_mode(mode, ok)
    base = {"mode": mode, "effective": eff, "calls": 0, "usd": 0.0, "confirmed": 0,
            "rejected": 0, "retimed": 0, "unsure": 0, "shots_flagged": 0, "errors": []}
    def unchecked(said: str) -> dict:
        # nothing went to Claude, but a shot may still rest on an old moment that a closer
        # look contradicted: it is listed all the same
        applied = apply_to_plan(plan, [], state, locked=locked, durations=duration_of,
                                contradicted_by=contradicted_by)
        base.update(said=verified_sentence({}, mode, 0, 0, said),
                    shots_flagged=len(applied["flagged"]), flagged=applied["flagged"],
                    contradicted=applied["contradicted"])
        return base
    if mode == "local":
        return unchecked("Local only: nothing was checked with Claude, "
                         "moments are marked unchecked.")
    events = telemetry_events_for(clips)
    if not events:
        return unchecked("No telemetry events here, so there was nothing "
                         "to check with Claude.")
    used_map = segments_using(plan.get("segments") or [], events)
    rows = route([*events, *claims], mode=mode, local_ok=ok, used=used_map.keys(),
                 heroes=hero_ids(events), limit=limit, verdicts=state["verdicts"])
    neigh = lambda e: [n for n in events if n["clip"] == e["clip"]
                       and _overlaps(float(e["start"]) - 2, float(e["end"]) + 2,
                                     float(n["start"]), float(n["end"]), 0.0)]
    summary = run_checks(rows, video_of=video_of, workdir=workdir, state=state, save=save,
                         duration_of=duration_of, neighbours_of=neigh, cancelled=cancelled)
    applied = apply_to_plan(plan, events, state, locked=locked, durations=duration_of,
                            contradicted_by=contradicted_by)
    out = {**base, **{k: summary[k] for k in ("calls", "usd", "confirmed", "rejected",
                                              "retimed", "unsure", "errors")},
           "shots_flagged": len(applied["flagged"]), "shots_widened": applied["retimed"],
           "flagged": applied["flagged"], "contradicted": applied["contradicted"],
           "routed": len(rows)}
    out["said"] = verified_sentence(summary, mode, len(applied["flagged"]), applied["retimed"])
    return out


def first_pass_price(mode: str, clips: Sequence[str], state: dict,
                     local_ok: bool | None = None) -> dict:
    """Upper bound shown before the first pass runs: the events the router could send
    (everything checkable that has no verdict yet), capped at the run's limit."""
    if mode == "local":
        return {"n": 0, "usd": 0.0, "label": "No Claude checks (Local only)",
                "mode": mode, **config.cost_terms()}
    ok = local_available() if local_ok is None else local_ok
    events = telemetry_events_for(clips)
    rows = route(events, mode=mode, local_ok=ok,
                 used=[e["event_id"] for e in events if is_checkable(e)],
                 heroes=hero_ids(events), limit=CALLS_PER_RUN, verdicts=state["verdicts"])
    p = price(len(rows), "moments")
    p["mode"] = mode
    return p
