#!/usr/bin/env python3
"""Footage-understanding eval, scored OFFLINE from a bin's sidecars (wave 20, lane EVAL).

    python3 tools/eval_understanding.py [--work DIR] [--bin NAME] [--eval DIR] [--out DIR]
                                        [--baseline FILE] [--check | --write-baseline]

No model call, no GPU, no write under --work: it reads the sidecars the board reads and
rebuilds, with the app's own code, what the planner and the board would assert today:

  index_raw  the old Claude-index events (sheets, close looks, deep looks), nothing demoted:
             the bake-off's "today's Claude index" row
  asserted   the ranked events after telemetry merge + `verify.demote_ranked` (what the
             board's list and the planner's event lines state as fact today)
  felt       the telemetry candidates alone (`check` events: airs, impacts, speed drops)
  confirmed  only what a checker settled: Claude confirm / retime verdicts and local-settled
             events (empty until `work/index/<bin>.verify.json` has verdicts)

against Karl's checked test set (`research/R12-understanding/eval/*.checked.json`):

  recall    labelled positives found within +-0.5 s, by kind (air incl. who=other, fall,
            camera-into-snow; camera-toss and water only count when a verdict says so), on
            the high-confidence rows and on the rows Karl confirmed (`karl: ok`)
  negatives how many of the 19 labelled negatives an event is asserted over, and false
            events per labelled-negative minute (the only minutes where precision is known)
  find      hit@3 for the non-low queries through the app's own `find.lexical` (the
            `find_*` lines), and through the app's own union of lexical + the 3080's PE-Core
            frame search (`find_union_*`, wave 20 FIND3): any overlap, and IoU >= 0.3. The union
            is scored from `<work>/local/<bin>/*.embed.npy` and the text vectors cached in
            `<eval>/query-vectors.json` (made once by `--refresh-vectors` on the 3080), so it
            needs no GPU; with either missing that line says "skipped". The Claude rerank is
            never in the gate: `--rerank` runs it (about one call per query) and reports it apart
  certainty counts by certainty of the events and held claims

THE REV, NOT A COPY (wave 21 EVALREV, verdict 16 punch 2). `--frozen DIR` scores the rev's own
pipeline instead of whatever an old 8766 pass left in a work dir: DIR is a frozen read-only copy of
a bin's inputs (`--freeze-from WORK --freeze-to DIR` copies visual / audio / telemetry / local, never
verdicts or video), and the checker's verdicts come from `<eval>/recorded-verdicts.json`
(`--capture-record WORK --rev SHA` writes it from one real pass's `index/<bin>.verify.json`, keeping
the model's answer before `verify.settle`). The tool then runs the rev's `verify.route` and
`verify.run_checks` (prompt, validate, settle, demotion, local join) with a scripted `inference`
backend that answers each strip from the record, keyed by event id. A first pass and a re-run, as the
judge's session did. No model call, no video; everything is written under --out. Exit 4 = the rev
routes events the recording never saw (re-record on a fresh pass; the gate shows AMBER), 3 = no
inputs or no recording (AMBER, "not measured"). The headline adds `checker_lost`: labelled
high-confidence events the sensor found that the checker threw out (baseline 0), and prints the
other riders' air row. A prompt change cannot show here (the answers are the old prompt's): re-record.

Scoring rules are the bake-off's (`eval/README.md`, `bench/score.py`): camera-toss is a
negative for fall / crash (a thrown camera is not a fall).

Writes `understanding.json` and `understanding.md` under --out. With --check it compares
the `asserted` + find numbers to the committed baseline (`eval/baseline.json`) and exits 1
on a regression beyond TOLERANCE, 0 otherwise (an improvement is printed with a hint to
update the baseline), 3 when the work dir is not on this machine. `--write-baseline`
writes the baseline from today's numbers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_WORK = Path("/mnt/roughcut/work/app-redesign")      # the Killington preview copy, read-only
DEFAULT_BIN = "killington-neutral"
DEFAULT_EVAL = ROOT / "research" / "R12-understanding" / "eval"
PAD = 0.5                                                   # seconds each side of a label
IOU_MIN = 0.3
FALL_FAMILY = {"fall", "crash", "camera-into-snow"}
EVENT_KINDS = ("air", "fall", "crash", "camera-into-snow", "camera-toss", "water")
INDEX_KIND = {"jump": "air", "air": "air", "fall": "fall", "crash": "crash"}   # index kind -> eval kind
TELEMETRY_KIND = {"air": "air", "impact": "crash", "speed_drop": "fall"}
LAYERS = ("index_raw", "asserted", "felt", "confirmed")

# A regression beyond these fails the gate. `higher` metrics may not fall by more than tol;
# `lower` metrics may not rise by more than tol. One labelled air is 0.14 of the 7 high-confidence
# airs, so any lost event in a small kind fails; the tolerance only absorbs float noise.
TOLERANCE = {
    "recall_high": ("higher", 0.05), "recall_karl": ("higher", 0.05),
    "recall_air": ("higher", 0.05), "recall_fall": ("higher", 0.05),
    "checker_lost": ("lower", 0),
    "negatives_asserted": ("lower", 1), "fa_per_neg_min": ("lower", 0.5),
    "find_overlap": ("higher", 0.05), "find_iou": ("higher", 0.05),
    "find_union_overlap": ("higher", 0.05), "find_union_iou": ("higher", 0.05),
}


# ------------------------------------------------------------------ small geometry

def stem(clip: str) -> str:
    return Path(str(clip)).stem.upper()


def overlaps(a0: float, a1: float, b0: float, b1: float, pad: float = 0.0) -> bool:
    """[a0,a1] against [b0,b1] widened by pad each side."""
    return a0 < b1 + pad and b0 - pad < a1


def iou(a: tuple, b: tuple) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


# ------------------------------------------------------------------ the eval set

def load_set(eval_dir: Path) -> tuple[list[dict], list[dict]]:
    """(moments, find queries). camera-toss positives stay positive for their own kind and
    also refute `fall` (bench rule: a tossed camera is not a fall)."""
    moments = json.loads((eval_dir / "killington-moments.checked.json").read_text())["moments"]
    queries = json.loads((eval_dir / "killington-find.checked.json").read_text())["queries"]
    out = []
    for m in moments:
        m = dict(m, clip=stem(m["clip"]))
        if m["kind"] == "camera-toss":
            m["refutes"] = sorted(set(m.get("refutes") or []) | {"fall"})
            m["negative_too"] = True
        out.append(m)
    return out, queries


def is_negative(m: dict) -> bool:
    return m["polarity"] == "negative" or bool(m.get("negative_too"))


def negatives(moments: list[dict]) -> list[dict]:
    return [m for m in moments if is_negative(m) and m.get("refutes")]


def neg_minutes(moments: list[dict]) -> float:
    by: dict[str, list] = {}
    for m in negatives(moments):
        by.setdefault(m["clip"], []).append((m["start"], m["end"]))
    total = 0.0
    for spans in by.values():
        spans.sort()
        cur = None
        for a, b in spans:
            if cur and a <= cur[1]:
                cur[1] = max(cur[1], b)
            else:
                if cur:
                    total += cur[1] - cur[0]
                cur = [a, b]
        if cur:
            total += cur[1] - cur[0]
    return total / 60


# ------------------------------------------------------------------ event scoring

def compatible(label_kind: str, pred_kind: str) -> bool:
    if label_kind == "air":
        return pred_kind == "air"
    if label_kind in ("fall", "camera-into-snow"):
        return pred_kind in FALL_FAMILY
    if label_kind in ("camera-toss", "water"):
        return pred_kind == label_kind
    return False


def refutes_pred(m: dict, pred_kind: str) -> bool:
    r = m.get("refutes") or []
    if pred_kind == "air":
        return "air" in r
    if pred_kind in FALL_FAMILY:
        return "fall" in r or "crash" in r
    return pred_kind in r


def rate(hits: list[bool]) -> list:
    return [round(sum(hits) / len(hits), 3), len(hits)] if hits else [None, 0]


def score_events(moments: list[dict], preds: list[dict]) -> dict:
    """preds: [{clip, start, end, kind}] with kind one of EVENT_KINDS (anything else is ignored)."""
    preds = [p for p in preds if p["kind"] in EVENT_KINDS]
    by_clip: dict[str, list] = {}
    for p in preds:
        by_clip.setdefault(p["clip"], []).append(p)

    def found(m: dict) -> bool:
        return any(compatible(m["kind"], p["kind"]) and overlaps(p["start"], p["end"], m["start"], m["end"], PAD)
                   for p in by_clip.get(m["clip"], []))

    rec: dict[str, list] = {}
    found_high: list[str] = []
    for m in moments:
        if m["polarity"] != "positive":
            continue
        k = m["kind"]
        scored = k == "air" or k in ("fall", "camera-into-snow")
        if k in ("camera-toss", "water"):
            rec.setdefault(f"extra:{k}", []).append(found(m))
        if not scored:
            continue
        hit = found(m)
        rec.setdefault(f"conf:{m['confidence']}", []).append(hit)
        if m["confidence"] == "high":
            if hit:
                found_high.append(m["id"])
            rec.setdefault("kind:air" if k == "air" else "kind:fall", []).append(hit)
            if k == "air":
                rec.setdefault(f"air_who:{m.get('who') or 'wearer'}", []).append(hit)
        if m.get("karl") == "ok":
            rec.setdefault("karl_ok", []).append(hit)

    negs = negatives(moments)
    asserted_neg = [m["id"] for m in negs if any(
        refutes_pred(m, p["kind"]) and overlaps(p["start"], p["end"], m["start"], m["end"], PAD)
        for p in by_clip.get(m["clip"], []))]
    false_events = 0
    unmatched = 0
    for p in preds:
        if p["kind"] in ("camera-toss", "water"):
            continue
        on_neg = any(m["clip"] == p["clip"] and refutes_pred(m, p["kind"])
                     and overlaps(p["start"], p["end"], m["start"], m["end"], PAD) for m in negs)
        on_pos = any(m["clip"] == p["clip"] and m["polarity"] == "positive" and compatible(m["kind"], p["kind"])
                     and overlaps(p["start"], p["end"], m["start"], m["end"], PAD) for m in moments)
        if on_neg:
            false_events += 1
        elif not on_pos:
            unmatched += 1
    mins = neg_minutes(moments)
    return {
        "recall": {k: rate(v) for k, v in sorted(rec.items())},
        "found_high": found_high,
        "negatives_asserted": [len(asserted_neg), len(negs)],
        "negatives_hit": asserted_neg,
        "false_events": false_events,
        "neg_minutes": round(mins, 2),
        "fa_per_neg_min": round(false_events / mins, 2) if mins else 0.0,
        "unmatched_events": unmatched,
        "events": len(preds),
    }


# ------------------------------------------------------------------ Find scoring

def score_find(queries: list[dict], search) -> dict:
    """`search(query) -> ranked [{clip,start,end}]`. hit@3 by overlap and by IoU >= 0.3."""
    res: dict[str, list] = {"hi_overlap": [], "hi_iou": [], "low_overlap": [], "low_iou": []}
    misses = []
    for q in queries:
        top = [(stem(r["clip"]), float(r["start"]), float(r["end"])) for r in search(q["query"])[:3]]
        ans = [(stem(a["clip"]), float(a["start"]), float(a["end"])) for a in q["answers"]]
        h_ov = any(c == ac and overlaps(s, e, a0, a1) for c, s, e in top for ac, a0, a1 in ans)
        h_io = any(c == ac and iou((s, e), (a0, a1)) >= IOU_MIN for c, s, e in top for ac, a0, a1 in ans)
        tier = "low" if q["confidence"] == "low" else "hi"
        res[f"{tier}_overlap"].append(h_ov)
        res[f"{tier}_iou"].append(h_io)
        if tier == "hi" and not h_ov:
            misses.append(q["id"])
    return {"hit3_overlap": rate(res["hi_overlap"]), "hit3_iou03": rate(res["hi_iou"]),
            "low_hit3_overlap": rate(res["low_overlap"]), "low_hit3_iou03": rate(res["low_iou"]),
            "misses": misses}


def score_rerank(queries: list[dict], usearch, work: Path, bin_name: str, cache: Path | None = None) -> dict:
    """The Claude rerank over the union's top rows, one call per non-low query (never in the gate).
    Thumbnails come from the bin's proxies; verdicts are cached in `cache` so a re-score is free."""
    import tempfile

    from roughcut import find as findmod
    hi = [q for q in queries if q["confidence"] != "low"]
    store = json.loads(cache.read_text()) if cache and cache.exists() else {}
    proxies = work / "proxies"
    thumbs = findmod.thumb_maker(lambda c: next((p for p in (proxies / bin_name / f"{stem(c)}.mp4", proxies / f"{stem(c)}.mp4")
                                                 if p.is_file()), proxies / f"{stem(c)}.mp4"),
                                 Path(tempfile.mkdtemp(prefix="find3-thumbs-")))
    calls, usd, ranked = 0, 0.0, {}
    for q in hi:
        base = usearch(q["query"])
        if q["query"] in store:
            order = store[q["query"]]
            ranked[q["query"]] = [base[i] for i in order if i < len(base)]
            continue
        got = findmod.rerank(q["query"], base, thumbs)
        calls += 1
        usd += got["usage"].get("projected_usd") or 0.0
        key = {(r["clip"], r["start"], r["end"]): i for i, r in enumerate(base)}
        store[q["query"]] = [key[(r["clip"], r["start"], r["end"])] for r in got["matches"]]
        ranked[q["query"]] = got["matches"]
        if cache:
            cache.write_text(json.dumps(store))
    res = score_find(hi, lambda qq: ranked[qq])
    return {**res, "calls": calls, "usd": round(usd, 3)}


# ------------------------------------------------------------------ building the predictions

def _verdict_kind(row: dict) -> str | None:
    v = row.get("verdict")
    if isinstance(v, dict) and v.get("verdict") in ("confirm", "retime") and v.get("kind") in EVENT_KINDS:
        return v["kind"]
    return None


def preds_from_ranked(rows: list[dict]) -> list[dict]:
    """Ranked / asserted event rows (events.py shape) as eval predictions."""
    out = []
    for r in rows:
        kind = _verdict_kind(r) or r.get("kind_checked") or INDEX_KIND.get(str(r.get("kind", "")).lower())
        if kind not in EVENT_KINDS:
            continue
        out.append({"clip": stem(r["clip"]), "start": float(r["start"]), "end": float(r["end"]),
                    "kind": kind, "certainty": r.get("certainty"), "source": r.get("source")})
    return out


def preds_from_felt(rows: list[dict]) -> list[dict]:
    """Annotated telemetry events (verify.annotate_event rows) that may be asserted."""
    out = []
    for r in rows:
        if r.get("handling") or not (r.get("check") or r.get("kind") == "air"):
            continue
        kind = _verdict_kind(r) or TELEMETRY_KIND.get(r.get("kind"))
        if kind not in EVENT_KINDS:
            continue
        v = r.get("verdict") if isinstance(r.get("verdict"), dict) else None
        s, e = r["start"], r["end"]
        if v and v.get("verdict") == "retime" and v.get("start") is not None and v.get("end") is not None:
            s, e = v["start"], v["end"]
        out.append({"clip": stem(r["clip"]), "start": float(s), "end": float(e), "kind": kind,
                    "certainty": r.get("certainty"), "asserted": r.get("asserted", True),
                    "source": "telemetry"})
    return out


def collect(work: Path, bin_name: str, record: dict | None = None, replay_dir: Path | None = None) -> dict:
    """Everything the layers need, from the sidecars only (read-only).

    With `record` (a recorded-verdicts file, see `load_record`) the verdicts are NOT read from
    `<work>/index/<bin>.verify.json`: the rev's own router picks the events to check and the
    rev's own checker code (prompt, `validate`, `settle`) takes the recorded answers from a
    scripted backend (`replay_state`), so a change in routing / settling / demotion shows."""
    from roughcut import events, find, telemetry_events as te, verify

    vis, aud, tel = work / "visual" / bin_name, work / "audio" / bin_name, work / "telemetry" / bin_name
    state = (verify.load_state(verify.state_path(work, bin_name)) if record is None
             else {"mode": verify.DEFAULT_MODE, "verdicts": {}})
    prev_dir = te._DIR
    te.use_telemetry_dir(tel)
    local_dir = work / "local" / bin_name

    def local_checks(clip: str) -> list[dict]:
        try:
            sc = json.loads((local_dir / f"{Path(clip).stem}.local.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [{**c, "model": sc.get("model", "")} for c in sc.get("checks") or []]

    verify.set_providers(telemetry=lambda c: te.telemetry_events(c, tel), local=local_checks)
    try:
        per_clip = events.gather(vis, aud)
        raw_clips = {c: {**d, "felt": None} for c, d in per_clip.items()}
        ranked_raw = events.rank_bin(raw_clips)
        ranked = events.rank_bin(per_clip)
        clips_all = sorted({Path(c).name for c in per_clip} | {p.name[: -len(".felt.json")] + ".MP4"
                                                               for p in tel.glob("*.felt.json")})
        replay = None
        if record is not None:
            replay = replay_state(clips_all, state, record, Path(replay_dir or tempfile.mkdtemp(prefix="replay-")))
        asserted, held = verify.demote_ranked(ranked, state)
        annotated = [verify.annotate_event(e, state, mode=state["mode"], local_ok=False)
                     for e in verify.telemetry_events_for(clips_all)]
        felt_all = [e for e in annotated if (e.get("check") or e.get("kind") == "air") and not e.get("handling")]
        confirmed = [e for e in annotated if e.get("asserted") and e.get("certainty") in ("claude", "local")]
        confirmed += [r for r in asserted if r.get("certainty") == "claude"]

        # Find's inventory, built as the server's _ask_clips does: transcript + demoted moments
        from roughcut import deep as deepmod
        clips: dict[str, dict] = {}
        for p in sorted(aud.glob("*.audio.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            clip, st = d["clip"], Path(d["clip"]).stem
            fine_p = vis / f"{st}.fine.json"
            fine = json.loads(fine_p.read_text(encoding="utf-8")) if fine_p.exists() else {}
            visp = vis / f"{st}.visual.json"
            coarse = json.loads(visp.read_text(encoding="utf-8")) if visp.exists() else {}
            moments = events.merge_moments(coarse.get("moments", []), fine, deepmod.load(vis, st))
            moments, _ = verify.demote(moments, clip, state)
            clips[clip] = {"clip": clip, "duration": d.get("duration_s"), "transcript": d.get("transcript", []),
                           "visual": {"moments": moments}}

        def search(query: str) -> list[dict]:
            return find.lexical(query, clips)
        return {"index_raw": preds_from_ranked(ranked_raw), "asserted": preds_from_ranked(asserted),
                "felt": preds_from_felt(felt_all),
                "confirmed": (preds_from_felt([e for e in confirmed if e.get("source") == "telemetry"])
                              + preds_from_ranked([e for e in confirmed if e.get("source") != "telemetry"])),
                "search": search, "clips": clips, "state": state, "held": held, "annotated": annotated,
                "ranked_raw": ranked_raw, "asserted_rows": asserted, "replay": replay}
    finally:
        verify.set_providers()
        te.use_telemetry_dir(prev_dir)


# ------------------------------------------------------------------ the recorded checker

RECORD = "recorded-verdicts.json"
RECORD_KEYS = ("verdict", "kind", "who", "start", "end", "confidence", "why")


def _strip_key(event_id: str) -> str:
    """The name `verify.check_event` gives an event's strip image (its stem)."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", event_id)


def extract_record(work: Path, bin_name: str, rev: str = "", note: str = "") -> dict:
    """The verdicts of one real pass (`<work>/index/<bin>.verify.json`) as a replayable record: the
    model's ANSWER (its `raw_verdict`, before `verify.settle` turned a low confidence into unsure),
    keyed by event id. What the rev does with the answer is the rev's code at replay time."""
    from roughcut import verify
    st = verify.load_state(verify.state_path(work, bin_name))
    out = {}
    for eid, v in sorted(st["verdicts"].items()):
        raw = {k: v.get(k) for k in RECORD_KEYS}
        raw["verdict"] = v.get("raw_verdict") or v.get("verdict")
        raw["model"] = v.get("model", "")
        out[eid] = raw
    return {"version": 1, "bin": bin_name, "rev": rev, "mode": st["mode"], "note": note,
            "captured_from": str(work), "verdicts": out}


def load_record(path: Path) -> dict | None:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("verdicts"), dict) else None


class RecordedChecker:
    """An `inference` backend that answers a checker call from the record, keyed by the strip image
    the call carries (named for the event id). An event with no record raises, which
    `verify.run_checks` stores as an error and counts: the rev routed something the recording
    never saw."""
    name = "recorded"

    def __init__(self, record: dict):
        self.by_key = {_strip_key(k): v for k, v in record["verdicts"].items()}
        self.answered: list[str] = []
        self.missing: list[str] = []

    def complete(self, request):
        from roughcut import inference
        key = Path(request.images[0]).stem if request.images else ""
        v = self.by_key.get(key)
        if v is None:
            self.missing.append(key)
            raise inference.InferenceError(f"no recorded verdict for {key}")
        self.answered.append(key)
        raw = json.dumps({k: v.get(k) for k in RECORD_KEYS})
        return inference.Result(content=None, input_tokens=0, output_tokens=0, backend=self.name,
                                model=v.get("model") or "recorded", projected_usd=0.0, latency_ms=0, raw=raw)


def replay_state(clips: list[str], state: dict, record: dict, workdir: Path, passes: int = 2) -> dict:
    """Run the rev's router and checker over the bin's telemetry events with the recorded answers.

    Mirrors the judge's session: a first pass and a re-run (the router skips what has a verdict),
    each limited to `verify.CALLS_PER_RUN`; the events the recorded pass checked count as used by the
    cut (the cut itself is not replayed), the router adds heroes, airs and doubtful falls by its own rules. Fills `state["verdicts"]`. No model call, no video, no strip drawn:
    `verify.build_strip` is replaced by a stub that only names the strip."""
    from roughcut import inference, verify
    events = verify.telemetry_events_for(clips)
    checker = RecordedChecker(record)
    neigh = lambda e: [n for n in events if n["clip"] == e["clip"]                       # noqa: E731
                       and verify._overlaps(float(e["start"]) - 2, float(e["end"]) + 2,
                                            float(n["start"]), float(n["end"]), 0.0)]

    def stub_strip(video, clip, start, end, out, *, duration=None):
        t0, w = verify.window_for(start, end, duration)
        times = [round(t0 + k * w / verify.STRIP_CELLS, 3) for k in range(verify.STRIP_CELLS)]
        return verify.Strip(Path(out), times, [0.0] * verify.STRIP_CELLS, (1566, 588), "recorded")

    old_strip, old_backend = verify.build_strip, inference._OVERRIDE
    old_ledger = os.environ.get("ROUGHCUT_LEDGER")
    Path(workdir).mkdir(parents=True, exist_ok=True)
    os.environ["ROUGHCUT_LEDGER"] = str(Path(workdir) / "replay-ledger.jsonl")
    verify.build_strip = stub_strip
    inference.set_backend(checker)
    routed_ids: set[str] = set()
    summaries = []
    try:
        for _ in range(passes):
            rows = verify.route(events, mode=state["mode"], local_ok=True,
                                used=[e["event_id"] for e in events if e["event_id"] in record["verdicts"]
                                      and verify.is_checkable(e)],
                                heroes=verify.hero_ids(events), limit=verify.CALLS_PER_RUN,
                                verdicts=state["verdicts"])
            routed_ids |= {r["event_id"] for r in rows}
            if not rows:
                break
            summaries.append(verify.run_checks(rows, video_of=lambda c: Path("/nonexistent") / c,
                                               workdir=Path(workdir), state=state, neighbours_of=neigh))
    finally:
        verify.build_strip = old_strip
        inference.set_backend(old_backend)
        if old_ledger is None:
            os.environ.pop("ROUGHCUT_LEDGER", None)
        else:
            os.environ["ROUGHCUT_LEDGER"] = old_ledger
    return {"recorded": len(record["verdicts"]), "routed": len(routed_ids), "answered": len(checker.answered),
            "unrecorded": sorted(set(checker.missing)),
            "not_routed": sorted(set(record["verdicts"]) - set(state["verdicts"])),
            "rev": record.get("rev", ""), "passes": len(summaries)}


def freeze_inputs(work: Path, bin_name: str, dest: Path | None) -> str:
    """Copy the sidecars the pipeline reads (never the verdicts, proxies or footage) to `dest` and
    make them read-only: the frozen input of the gate's understanding check."""
    import shutil
    if dest is None:
        return "--freeze-to is required"
    for d in ("visual", "audio", "telemetry", "local"):
        src = Path(work) / d / bin_name
        if src.is_dir():
            shutil.copytree(src, Path(dest) / d / bin_name, dirs_exist_ok=True)
    for root, dirs, files in os.walk(dest):
        for n in files:
            os.chmod(Path(root) / n, 0o444)
        for n in dirs:
            os.chmod(Path(root) / n, 0o555)
    os.chmod(dest, 0o555)
    return f"froze {bin_name} from {work} to {dest} (read-only)"


def certainty_counts(data: dict) -> dict:
    """Counts by certainty: telemetry events (annotated) and the index claims that were held."""
    ev: dict[str, int] = {}
    for e in data["annotated"]:
        if (e.get("check") or e.get("kind") == "air") and not e.get("handling"):
            ev[e["certainty"]] = ev.get(e["certainty"], 0) + 1
    held: dict[str, int] = {}
    for h in data["held"]:
        held[h.get("certainty", "?")] = held.get(h.get("certainty", "?"), 0) + 1
    asserted = {}
    for r in data["asserted_rows"]:
        if r.get("certainty"):
            asserted[r["certainty"]] = asserted.get(r["certainty"], 0) + 1
    return {"telemetry_events": ev, "index_claims_asserted": asserted, "index_claims_held": held,
            "verdicts": len(data["state"].get("verdicts", {})), "mode": data["state"].get("mode")}


VECTORS = "query-vectors.json"


def load_vectors(eval_dir: Path) -> dict:
    p = Path(eval_dir) / VECTORS
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def refresh_vectors(eval_dir: Path, queries: list[dict]) -> str:
    """Encode every query on the 3080 and cache the vectors next to the test set."""
    from roughcut import localvlm
    r = localvlm.encode_texts([q["query"] for q in queries])
    if not r.get("ok"):
        return f"skipped: no local stack ({r.get('why')})"
    (Path(eval_dir) / VECTORS).write_text(json.dumps(
        {"model": r["model"], "vectors": {t: [round(x, 4) for x in v] for t, v in r["vectors"].items()}}) + "\n")
    return f"wrote {Path(eval_dir) / VECTORS}"


def local_search_from(local_dir: Path, eval_dir: Path, queries: list[dict], clips: dict | None = None):
    """(`local_search(text, k)` for `find.search`, '') built from the embeddings and the cached text
    vectors, or (None, why) when either is missing. Pure numpy, no GPU."""
    from roughcut import localvlm
    items = localvlm.load_items(local_dir) if Path(local_dir).is_dir() else []
    if not items:
        return None, f"skipped: no frame embeddings in {local_dir} (the 3080 stage has not run)"
    left = sorted({stem(c) for c in clips or {}} - {c for c, _, _ in items})
    if left:
        return None, f"skipped: the 3080 stage has embedded {len(items)} of {len(items) + len(left)} clips so far"
    vecs = load_vectors(eval_dir).get("vectors") or {}
    missing = [q["query"] for q in queries if q["query"] not in vecs]
    if missing:
        return None, f"skipped: no cached text vectors for {len(missing)} queries (run --refresh-vectors on the 3080)"

    def local_search(text, k=3, **_):
        return {"ok": True, "why": "", "results": {text: localvlm.rank_windows(vecs[text], items, k)}}
    return local_search, ""


def union_find(data: dict, queries: list[dict], local_dir: Path, eval_dir: Path):
    """-> (search(query) -> ranked rows | None, skipped-why). The app's own `find.search`."""
    from roughcut import find
    ls, why = local_search_from(local_dir, eval_dir, queries, data.get("clips"))
    if ls is None:
        return None, why
    return (lambda q: find.search(q, data["clips"], local_search=ls)["matches"]), ""


def evaluate(work: Path, bin_name: str, eval_dir: Path, local_dir: Path | None = None,
             rerank: bool = False, rerank_cache: Path | None = None, record: dict | None = None,
             replay_dir: Path | None = None) -> dict:
    moments, queries = load_set(eval_dir)
    data = collect(work, bin_name, record, replay_dir)
    layers = {k: score_events(moments, data[k]) for k in LAYERS}
    find = score_find(queries, data["search"])
    usearch, why = union_find(data, queries, local_dir or (work / "local" / bin_name), eval_dir)
    find_union = score_find(queries, usearch) if usearch else {"skipped": why}
    if usearch and rerank:
        find_union["rerank"] = score_rerank(queries, usearch, work, bin_name, rerank_cache)
    return {"bin": bin_name, "work": str(work), "layers": layers, "find": find, "find_union": find_union,
            "certainty": certainty_counts(data), "replay": data.get("replay"),
            "checker_lost": sorted(set(layers["felt"]["found_high"]) - set(layers["asserted"]["found_high"])),
            "set": {"positives": sum(1 for m in moments if m["polarity"] == "positive"),
                    "negatives": len(negatives(moments)), "queries": len(queries)},
            "headline": headline(layers["asserted"], find, find_union,
                                 len(sorted(set(layers["felt"]["found_high"]) - set(layers["asserted"]["found_high"]))))}


# ------------------------------------------------------------------ headline, baseline, gate

def headline(asserted: dict, find: dict, find_union: dict | None = None, checker_lost: int = 0) -> dict:
    fu = find_union if find_union and "skipped" not in find_union else None
    r = asserted["recall"]
    pick = lambda k: (r.get(k) or [None, 0])[0]                      # noqa: E731
    return {"recall_high": pick("conf:high"), "recall_karl": pick("karl_ok"),
            "recall_air": pick("kind:air"), "recall_fall": pick("kind:fall"),
            "recall_other_air": pick("air_who:other"),
            "checker_lost": checker_lost,
            "negatives_asserted": asserted["negatives_asserted"][0],
            "negatives_total": asserted["negatives_asserted"][1],
            "fa_per_neg_min": asserted["fa_per_neg_min"],
            "find_overlap": find["hit3_overlap"][0], "find_iou": find["hit3_iou03"][0],
            "find_union_overlap": fu["hit3_overlap"][0] if fu else None,
            "find_union_iou": fu["hit3_iou03"][0] if fu else None}


def compare(base: dict, cur: dict, tol: dict | None = None) -> tuple[list[str], list[str]]:
    """(regressions, improvements) of headline `cur` against `base`."""
    tol = tol or TOLERANCE
    bad, good = [], []
    for k, (direction, t) in tol.items():
        b, c = base.get(k), cur.get(k)
        if b is None or c is None:
            continue
        delta = c - b
        worse = -delta if direction == "higher" else delta
        if worse > t + 1e-9:
            bad.append(f"{k} {b} -> {c}")
        elif -worse > t + 1e-9:
            good.append(f"{k} {b} -> {c}")
    return bad, good


def _f(x) -> str:
    return "-" if x is None else f"{x:.2f}"


def gate_line(res: dict, base: dict | None) -> tuple[bool, str]:
    h = res["headline"]
    line = (f"recall {_f(h['recall_high'])} (karl-confirmed {_f(h['recall_karl'])}; air {_f(h['recall_air'])}, "
            f"fall {_f(h['recall_fall'])}) · {h['negatives_asserted']} of {h['negatives_total']} negatives asserted · "
            f"{_f(h['fa_per_neg_min'])} false events/negative-min · Find hit@3 {_f(h['find_overlap'])} "
            f"(IoU>=0.3 {_f(h['find_iou'])}) · Find + 3080 "
            + (f"{h['find_union_overlap']:.2f} (IoU>=0.3 {h['find_union_iou']:.2f})"
               if h.get("find_union_overlap") is not None else "skipped: " + str((res.get("find_union") or {}).get("skipped", "no local stack here")).removeprefix("skipped: ")))
    oa = h.get("recall_other_air")
    line += f" · other riders' airs {'-' if oa is None else f'{oa:.2f}'}"
    rp = res.get("replay")
    if rp:
        line += (f" · the rev's own router + checker over {rp['recorded']} recorded Claude verdicts"
                 f" ({rp['rev'] or '?'}): {rp['answered']} of {rp['routed']} routed events answered, "
                 f"{h['checker_lost']} labelled high-confidence event(s) the sensor found and the checker lost")
        if rp["unrecorded"]:
            line += f" · STALE RECORDING: the rev routes {len(rp['unrecorded'])} event(s) it never saw (re-record)"
    if base is None:
        return True, line + " · no baseline to compare"
    bad, good = compare(base["headline"], h)
    if bad:
        return False, line + " · REGRESSION vs baseline: " + "; ".join(bad)
    if good:
        return True, line + " · improved vs baseline: " + "; ".join(good) + \
            " (update research/R12-understanding/eval/baseline.json: tools/eval_understanding.py --write-baseline)"
    return True, line + " · matches baseline"


def markdown(res: dict) -> str:
    L = res["layers"]
    names = {"index_raw": "Claude index (raw)", "asserted": "Asserted today", "felt": "Felt (telemetry)",
             "confirmed": "Checker-confirmed"}

    def r(layer, key):
        v = L[layer]["recall"].get(key) or [None, 0]
        return "-" if v[0] is None else f"{v[0]:.2f} ({round(v[0] * v[1])}/{v[1]})"
    out = [f"# Understanding eval · {res['bin']}", "",
           f"Set: {res['set']['positives']} positives, {res['set']['negatives']} negatives, {res['set']['queries']} Find queries. "
           f"Offline, from sidecars in {res['work']}.", "",
           "| Layer | Recall high | Karl-confirmed | Air | Fall | Negatives asserted | False/neg-min | Events |",
           "|---|---|---|---|---|---|---|---|"]
    for k in LAYERS:
        x = L[k]
        out.append(f"| {names[k]} | {r(k, 'conf:high')} | {r(k, 'karl_ok')} | {r(k, 'kind:air')} | {r(k, 'kind:fall')} | "
                   f"{x['negatives_asserted'][0]} of {x['negatives_asserted'][1]} | {x['fa_per_neg_min']:.2f} | {x['events']} |")
    f = res["find"]
    out += ["", f"Find hit@3 (non-low queries): overlap {f['hit3_overlap'][0]} (n={f['hit3_overlap'][1]}), "
            f"IoU>=0.3 {f['hit3_iou03'][0]}; low-confidence queries: overlap {f['low_hit3_overlap'][0]} (n={f['low_hit3_overlap'][1]}). "
            f"Misses: {', '.join(f['misses']) or 'none'}."]
    fu = res.get("find_union") or {}
    if "skipped" in fu:
        out += ["", f"Find + 3080: {fu['skipped']}"]
    elif fu:
        out += ["", f"Find + 3080 (lexical + PE-Core union): overlap {fu['hit3_overlap'][0]}, IoU>=0.3 {fu['hit3_iou03'][0]}; "
                f"low-confidence: overlap {fu['low_hit3_overlap'][0]}. Misses: {', '.join(fu['misses']) or 'none'}."]
        if fu.get("rerank"):
            rr = fu["rerank"]
            out += ["", f"Find + 3080 + Claude rerank ({rr['calls']} calls, ${rr['usd']}): overlap {rr['hit3_overlap'][0]}, "
                    f"IoU>=0.3 {rr['hit3_iou03'][0]}. Misses: {', '.join(rr['misses']) or 'none'}."]
    rp = res.get("replay")
    if rp:
        out += ["", f"Recorded checker ({rp['rev'] or '?'}): {rp['recorded']} recorded verdicts, {rp['passes']} pass(es), "
                f"{rp['routed']} events routed by this rev, {rp['answered']} answered from the record, "
                f"{len(rp['unrecorded'])} routed with no record ({', '.join(rp['unrecorded'][:8]) or 'none'}), "
                f"{len(rp['not_routed'])} recorded but not routed ({', '.join(rp['not_routed'][:8]) or 'none'}). "
                f"Found by the sensor, lost by the checker: {', '.join(res['checker_lost']) or 'none'}."]
    out += ["",
            f"Certainty: {json.dumps(res['certainty'])}"]
    ex = {k: v for k, v in L["asserted"]["recall"].items() if k.startswith(("extra:", "air_who:"))}
    if ex:
        out += ["", "Asserted, extra rows: " + ", ".join(f"{k} {v[0]} (n={v[1]})" for k, v in ex.items())]
    out += ["", "Negatives still asserted: " + (", ".join(L["asserted"]["negatives_hit"]) or "none")]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", type=Path, default=DEFAULT_WORK)
    ap.add_argument("--bin", default=DEFAULT_BIN)
    ap.add_argument("--eval", type=Path, default=DEFAULT_EVAL)
    ap.add_argument("--out", type=Path, default=None, help="where understanding.json / .md go (default: not written)")
    ap.add_argument("--baseline", type=Path, default=None)
    ap.add_argument("--local", type=Path, default=None, help="where the .embed.npy files are (default <work>/local/<bin>)")
    ap.add_argument("--refresh-vectors", action="store_true", help="encode the Find queries on the 3080 into <eval>/query-vectors.json")
    ap.add_argument("--rerank", action="store_true", help="also score the Claude rerank (one call per non-low query; not for the gate)")
    ap.add_argument("--rerank-cache", type=Path, default=None)
    ap.add_argument("--frozen", type=Path, default=None,
                    help="score the REV: a frozen read-only copy of a bin's inputs (see --freeze-from) with the recorded "
                         "checker verdicts replayed through the rev's own router and checker (implies --work)")
    ap.add_argument("--record", type=Path, default=None, help=f"recorded verdicts (default <eval>/{RECORD})")
    ap.add_argument("--capture-record", type=Path, default=None, metavar="WORK",
                    help="write --record from the verdicts of the real pass in WORK (<WORK>/index/<bin>.verify.json)")
    ap.add_argument("--rev", default="", help="the rev the captured pass ran (goes in the record)")
    ap.add_argument("--freeze-from", type=Path, default=None, metavar="WORK",
                    help="copy WORK's visual / audio / telemetry / local sidecars of --bin to --freeze-to, read-only")
    ap.add_argument("--freeze-to", type=Path, default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write-baseline", action="store_true")
    a = ap.parse_args(argv)
    baseline = a.baseline or (a.eval / "baseline.json")
    record_path = a.record or (a.eval / RECORD)
    if a.freeze_from:
        print(freeze_inputs(a.freeze_from, a.bin, a.freeze_to))
        return 0
    if a.capture_record:
        rec = extract_record(a.capture_record, a.bin, a.rev, "captured by tools/eval_understanding.py --capture-record")
        record_path.write_text(json.dumps(rec, indent=1) + "\n")
        print(f"wrote {record_path}: {len(rec['verdicts'])} verdicts")
        return 0
    record = None
    if a.frozen:
        a.work = a.frozen
        record = load_record(record_path)
        if record is None:
            print(f"understanding: skipped, no recorded checker verdicts at {record_path}")
            return 3
    if not (a.work / "telemetry" / a.bin).is_dir() and not (a.work / "visual" / a.bin).is_dir():
        print(f"understanding: skipped, no work dir for {a.bin} at {a.work}")
        return 3
    if a.refresh_vectors:
        print(refresh_vectors(a.eval, load_set(a.eval)[1]))
    res = evaluate(a.work, a.bin, a.eval, a.local, rerank=a.rerank, rerank_cache=a.rerank_cache,
                   record=record, replay_dir=(a.out / "replay") if a.out else None)
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "understanding.json").write_text(json.dumps(res, indent=1))
        (a.out / "understanding.md").write_text(markdown(res))
    if a.write_baseline:
        baseline.write_text(json.dumps({"bin": res["bin"], "note": "written by tools/eval_understanding.py --write-baseline",
                                        "headline": res["headline"], "layers": {k: {kk: v for kk, v in x.items() if kk != "negatives_hit"}
                                                                                  for k, x in res["layers"].items()},
                                        "find": {k: v for k, v in res["find"].items() if k != "misses"},
                                        "find_union": {k: v for k, v in res["find_union"].items() if k not in ("misses", "rerank")}}, indent=1) + "\n")
        print(f"wrote {baseline}")
    base = json.loads(baseline.read_text()) if baseline.exists() else None
    ok, line = gate_line(res, base)
    print(markdown(res) if not a.check else "")
    print("understanding:", line)
    if a.check and not ok:
        return 1
    if a.check and (res.get("replay") or {}).get("unrecorded"):
        return 4          # not a regression, but the recording no longer covers what the rev routes
    return 0


if __name__ == "__main__":
    sys.exit(main())
