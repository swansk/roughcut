# /// script
# requires-python = ">=3.12"
# dependencies = ["fastapi>=0.115", "uvicorn>=0.30"]
# ///
"""Roughcut cut board — the human's half of the loop, as a local web app.

Karl's brief for this, after watching the first two cuts:

    "it should provide an easy to use interface for the human to interject / ask for
     edits / set the scene and story... make that fine tuning fun and easy."

So the design constraint that governs everything here is **latency**. Fine-tuning is
only fun if trimming a cut point and seeing the result are the same gesture. Two
consequences:

  * The browser never touches the 5.3K HEVC masters. Every clip gets a 720p proxy
    once, and the UI plays those. Seeking a proxy is instant; seeking the master is
    not, and an editor that stutters on every scrub is one nobody opens twice.
  * Media is served with byte-range support. Without it a <video> element cannot
    seek at all, it can only stream from zero, which would make per-segment preview
    useless no matter how small the proxy is.

State lives in the EDL JSON on disk — the same file `assemble.py` renders and
`edl_snap.py` rewrites. The app is a view over that file, not a new source of truth,
so anything done here stays scriptable and anything scripted stays visible here.

The one required argument is a folder of footage. Karl, after using the first
version: *"App is pretty hard to use right now — unclear how to go from start to
finish."* It was a refinement tool that assumed five terminal steps had already
happened, one of which was hand-authoring the EDL it opens. So a missing EDL is not
an error here: it is scaffolded, empty, next to the project's other derived files,
and the app's job is to fill it.

Usage:
    uv run app/server.py --footage ~/footage/copper-02-2026

    # or against an EDL and sidecars that already exist
    uv run app/server.py --footage ~/footage/copper-02-2026 \
        --edl research/edl/B1-variantB.json --sidecars ~/work/audio
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, Response,
                               StreamingResponse)
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roughcut import (config, dictate, effects, events, find, inference,  # noqa: E402
                      picks, progress, revise, selects)

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "research" / "tools"
PROXY_W = 1280
PROXY_CRF = 26
# Kept in step with audio_analyze.py — the two must agree on what counts as footage,
# or the "N clips, M analysed" the UI shows would never reach parity.
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".webm"}

app = FastAPI()
STATE: dict = {}
# Four registries, one job shape. They stay separate because each kind has its own
# "is one already running" rule and its own status endpoint, and they all hold
# `progress.Job` — a dict subclass — so every existing reader, mutation and
# `{**entry}` in this file keeps working while the shared bar reads one shape.
RENDERS: dict[str, progress.Job] = {}
ANALYSES: dict[str, progress.Job] = {}
ASKS: dict[str, progress.Job] = {}
VISUALS: dict[str, progress.Job] = {}
FINDS: dict[str, progress.Job] = {}


def all_jobs() -> list[progress.Job]:
    """Every long operation this server knows about, of any kind.

    Karl: *"consider a progress tracking bar up top for anything which may take time
    to complete — re-use across app."* One list is what makes that one bar possible,
    and it is what lets a reloaded page re-attach to a render and an Ask that are both
    still running — which is the state his machine is in as this is written.
    """
    return [*ANALYSES.values(), *VISUALS.values(), *ASKS.values(), *FINDS.values(),
            *RENDERS.values()]


# Polled once a second by every open board, so it carries no payloads: a finished Ask
# holds a 15k-token plan and a render holds ffmpeg's whole log, and neither belongs in
# a heartbeat. Both stay one fetch away on /api/job/{id}.
JOB_LIST_OMIT = ("plan", "found")


@app.get("/api/jobs")
def api_jobs() -> JSONResponse:
    """What is happening right now. The whole top bar polls this and nothing else."""
    out = []
    for snap in progress.live(all_jobs()):
        row = {k: v for k, v in snap.items() if k not in JOB_LIST_OMIT}
        if row.get("log"):
            row["log"] = "\n".join(str(row["log"]).splitlines()[-6:])
        out.append(row)
    return JSONResponse({"jobs": out})


@app.get("/api/job/{job}")
def api_job(job: str) -> JSONResponse:
    for registry in (ANALYSES, VISUALS, ASKS, FINDS, RENDERS):
        if job in registry:
            return JSONResponse(registry[job].snapshot())
    raise HTTPException(404, "no such job")


# ---------------------------------------------------------------- proxies


def build_proxy(src: Path, dest: Path, orient: str) -> None:
    # Written to a temp name and renamed, because the server is already serving while
    # this thread runs: a half-written file at the final path is handed to a <video>
    # element as a truncated stream, which fails to decode and is then cached as
    # broken until a reload. os.replace is atomic within a filesystem.
    # Unique per process, not just per clip. Work dirs are shared by default now, so
    # two servers pointed at the same bin — easily done — would otherwise both write
    # the same .part path and hand the survivor a file interleaved from two encodes.
    tmp = dest.with_suffix(f".{os.getpid()}.part.mp4")
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin"]
    if orient == "none":
        # Same lesson as assemble.py: -noautorotate leaves the display matrix on the
        # output, and a proxy that plays sideways in the UI is worse than no proxy.
        cmd += ["-display_rotation", "0"]
    cmd += [
        "-i", str(src), "-map", "0:v:0", "-map", "0:a:0", "-dn",
        # Capped by width so a source smaller than the target is left alone rather
        # than upscaled into a bigger file that carries no more detail.
        "-vf", f"scale=min({PROXY_W}\\,iw):-2", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", str(PROXY_CRF), "-c:a", "aac", "-b:a", "128k",
        "-write_tmcd", "0", "-movflags", "+faststart", str(tmp),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"proxy failed for {src.name}: {r.stderr[-300:]}")
    tmp.replace(dest)


def ensure_proxies(clips: list[str], progress=None) -> None:
    """Build the missing 720p proxies, reporting how far along it is.

    Encoding a long bin takes tens of minutes — Killington's 44 minutes of 5.3K is
    about half an hour — and it happens in a background thread while the board is
    already usable. Karl, looking at exactly that: *"Need more indication of what is
    actually going on in the tool UI itself."* So the count lives in STATE where
    /api/status can read it, not only in a callback the analysis job passes in.
    """
    pdir: Path = STATE["proxy_dir"]
    pdir.mkdir(parents=True, exist_ok=True)
    todo = [c for c in clips if not (pdir / f"{Path(c).stem}.mp4").exists()]
    if todo:
        print(f"building {len(todo)} proxies (once per clip)...", flush=True)

    def report(done: int) -> None:
        STATE["proxy_done"], STATE["proxy_total"] = done, len(todo)
        if progress:
            progress(done, len(todo))

    report(0)
    for i, clip in enumerate(todo, 1):
        src = STATE["footage"] / clip
        if not src.exists():
            print(f"  !! missing footage {clip}", flush=True)
            continue
        build_proxy(src, pdir / f"{Path(clip).stem}.mp4", STATE["orient"])
        print(f"  [{i}/{len(todo)}] {clip}", flush=True)
        report(i)
    STATE["proxies_ready"] = True
    print("proxies ready", flush=True)


# ---------------------------------------------------------------- project io


def scaffold_edl(path: Path, footage: Path, orient: str) -> dict:
    """Write an empty project for a bin that has never been cut.

    Deliberately the same shape `assemble.py` and `edl_snap.py` already read, with
    zero segments — an empty edit is a valid edit, and making the app able to *create*
    one is what removes the hand-authored-JSON prerequisite. `orient` is a per-clip
    property that cannot be generalised across bins (B1 Copper's rotation side-data is
    spurious, B2 Killington's is correct), so it stays an explicit choice rather than
    something guessed here.
    """
    edl = {
        "variant": "", "title": footage.name, "orient": orient, "story": "",
        "target_s": [120, 180], "segments": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(edl, indent=1), encoding="utf-8")
    return edl


def footage_clips() -> list[str]:
    footage: Path = STATE["footage"]
    if not footage.is_dir():
        return []
    return sorted(p.name for p in footage.iterdir()
                  if p.suffix.lower() in VIDEO_SUFFIXES)


def analysed_stems() -> set[str]:
    return {p.name[: -len(".audio.json")]
            for p in STATE["sidecars"].glob("*.audio.json")}


def capture_time(clip: str) -> float | None:
    """When this clip was recorded, as an epoch. Cached — it costs an ffprobe.

    The container's `creation_time` first, the file's mtime as a fallback. Without
    this the model has no idea what "before" means and will happily cut from a 22:17
    shot back to a 20:53 one, which is exactly what Karl saw in the airport section of
    the first originated cut.
    """
    cache: dict = STATE.setdefault("capture", {})
    if clip in cache:
        return cache[clip]
    src: Path = STATE["footage"] / clip
    when: float | None = None
    if src.exists():
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time",
             "-of", "csv=p=0", str(src)], capture_output=True, text=True)
        raw = r.stdout.strip().strip(",")
        if raw:
            try:
                when = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except ValueError:
                when = None
        if when is None:
            when = src.stat().st_mtime
    cache[clip] = when
    return when


def load_sidecar(clip: str) -> dict:
    p: Path = STATE["sidecars"] / f"{Path(clip).stem}.audio.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def load_visual(clip: str) -> dict:
    """The visual pass's sidecar, if anyone has run it over this bin.

    Optional by design: the audio pass is cheap and local, this one costs model calls,
    so a project may have one and not the other. Nothing here requires it — but the
    moments it carries are the only record of events nobody narrated.
    """
    stem = Path(clip).stem
    p: Path = STATE["visual"] / f"{stem}.visual.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    fine = load_fine(stem)
    # The close look replaces the coarse account of the same seconds rather than
    # joining it. An inventory that still lists a backflip the 1s read found to be a
    # glove over the lens teaches the Ask exactly the wrong thing (R10).
    return {"moments": events.merge_moments(d.get("moments", []), fine),
            "unusable": d.get("unusable", []) + fine.get("unusable", []),
            "summary": d.get("summary", "")}


def load_fine(stem: str) -> dict:
    p: Path = STATE["visual"] / f"{stem}.fine.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def visual_stems() -> set[str]:
    return {p.name[: -len(".visual.json")]
            for p in STATE["visual"].glob("*.visual.json")}


def fine_stems() -> set[str]:
    return {p.name[: -len(".fine.json")]
            for p in STATE["visual"].glob("*.fine.json")}


# The visual pass costs model calls, so it is priced before it is offered. One sheet is
# 30 cells at 4s (visual_pass.py's defaults) and measured $0.09 on CLIP_01 (two sheets,
# $0.18) — provisional like everything about that pass (RQ-1/RQ-7 are unmeasured).
VISUAL_SHEET_S = 120.0
VISUAL_USD_PER_SHEET = 0.09

# The second stage: a close look at the few busiest windows in each clip. Priced the
# same way and from the same kind of evidence — 23 fine sheets over three Killington
# clips came to $1.68, i.e. $0.073 each, barely under a coarse sheet despite covering
# eight seconds instead of two minutes, because the prompt and not the image is most of
# the input. Three windows per clip by default: enough to audit a clip's loudest
# claims, cheap enough that turning the stage on is not a decision.
FINE_WINDOWS_PER_CLIP = 3
FINE_USD_PER_WINDOW = 0.073

# And how long each of those costs in wall clock, which is what a progress bar needs
# and the price does not say. Measured on Killington: a coarse sheet ~40s, a fine
# window ~35s (same prompt, fewer frames), the free motion scan ~6s per 5-minute clip.
VISUAL_SHEET_READ_S = 40.0
FINE_WINDOW_READ_S = 35.0
SCAN_S_PER_CLIP = 8.0


def clip_duration(clip: str) -> float | None:
    """Seconds of footage in a clip: from its audio sidecar when analysed, otherwise
    one ffprobe, cached either way."""
    cache: dict = STATE.setdefault("durations", {})
    if clip in cache:
        return cache[clip]
    d = load_sidecar(clip).get("duration_s")
    if d is None:
        src: Path = STATE["footage"] / clip
        d = probe_duration(src) if src.exists() else None
    cache[clip] = d
    return d


def visual_status(fine: bool = True) -> dict:
    """How much of the bin has been looked at, and what looking at the rest would cost.

    Never run on its own: the audio pass is local and free, this one spends a model call
    per sheet, so the board offers it with a price and a count and the human clicks.

    Both stages are priced, and the second stage's price is reported separately as well
    as in the total — a button that silently grew 40% dearer because a default changed
    is the opposite of offering a price.
    """
    clips = footage_clips()
    done = visual_stems()
    pending = [c for c in clips if Path(c).stem not in done]
    sheets = sum(max(1, math.ceil((clip_duration(c) or 0.0) / VISUAL_SHEET_S))
                 for c in pending)
    # The close look is per clip, not per pending clip: a clip already read coarsely
    # but never audited is exactly the one whose loudest claim is unchecked.
    seen_closely = fine_stems()
    fine_clips = [c for c in clips if Path(c).stem not in seen_closely]
    fine_calls = len(fine_clips) * FINE_WINDOWS_PER_CLIP if fine else 0
    return {
        "done": len(clips) - len(pending), "total": len(clips), "pending": pending,
        "calls": sheets + fine_calls,
        "coarse_calls": sheets, "fine_calls": fine_calls,
        "fine_pending": len(fine_clips), "fine_done": len(clips) - len(fine_clips),
        "projected_usd": round(sheets * VISUAL_USD_PER_SHEET
                               + fine_calls * FINE_USD_PER_WINDOW, 2),
        "running": any(v["state"] not in progress.TERMINAL for v in VISUALS.values()),
        "events": len(events.load(STATE["visual"])),
        "dir": str(STATE["visual"]),
    }


def read_edl() -> dict:
    return json.loads(STATE["edl"].read_text(encoding="utf-8"))


def write_edl(edl: dict) -> None:
    """Atomically. The board autosaves on every edit while its own status polls, the
    monitor and any render job read the same file; a plain write truncates first, and a
    reader landing in that gap sees an empty file and fails to parse it. Found by a test
    polling the EDL during a save — the same lesson as the proxies, one file over."""
    tmp = STATE["edl"].with_name(STATE["edl"].name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(edl, indent=1), encoding="utf-8")
    tmp.replace(STATE["edl"])


def project_payload() -> dict:
    edl = read_edl()
    segments = []
    for i, s in enumerate(edl["segments"]):
        segments.append({"id": s.get("id") or f"s{i}", **s})

    clips: dict[str, dict] = {}
    referenced = {s["clip"] for s in segments}
    # Every clip with a sidecar is offered, not just the ones already used — adding a
    # shot you forgot is as much a part of fine-tuning as trimming one you have.
    for p in sorted(STATE["sidecars"].glob("*.audio.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        clip = d["clip"]
        clips[clip] = {
            "clip": clip,
            "stem": Path(clip).stem,
            "duration": d["duration_s"],
            "proxy": f"/media/proxy/{Path(clip).stem}.mp4",
            # The card's picture: this URL plus ?t=<in-point>. A card is a still, not
            # a stream — see /media/poster for what sixteen streams cost the monitor.
            "poster": f"/media/poster/{Path(clip).stem}.jpg",
            "transcript": d.get("transcript", []),
            "visual": load_visual(clip),
            "captured": capture_time(clip),
            "candidates": d.get("candidates", [])[:12],
            "summary": {k: d["summary"].get(k) for k in
                        ("speech_fraction", "wind_dominant_fraction",
                         "integrated_lufs", "audio_usable")},
            "used": clip in referenced,
        }
    return {
        "title": edl.get("title", ""),
        "variant": edl.get("variant", ""),
        "reading": edl.get("reading", ""),
        "story": edl.get("story", ""),
        "target": edl.get("target_s", [120, 180]),
        "segments": segments,
        "clips": clips,
        # The bin's events in priority order — one list, not one per clip, because the
        # question is "what are the biggest things in this footage" and that is not a
        # per-clip question. Capped: the board shows 40 and the Ask reads 14, so a
        # 400-event bin does not put 400 rows through every poll.
        "events": events.load(STATE["visual"])[:60],
        "proxies_ready": STATE.get("proxies_ready", False),
        "edl_path": str(STATE["edl"]),
        "music": edl.get("effects_music"),
    }


@app.get("/api/project")
def api_project() -> JSONResponse:
    return JSONResponse(project_payload())


@app.get("/api/status")
def api_status() -> JSONResponse:
    """Where this project actually is, in terms of the steps it has to go through.

    The board used to answer only "what is in the edit"; a human who had not run the
    terminal steps got an empty screen with nothing to explain it. This says how many
    clips exist, how many are analysed, and where the files are.
    """
    clips = footage_clips()
    done = analysed_stems()
    pending = [c for c in clips if Path(c).stem not in done]
    return JSONResponse({
        "footage": str(STATE["footage"]),
        "footage_exists": STATE["footage"].is_dir(),
        "sidecars": str(STATE["sidecars"]),
        "edl": str(STATE["edl"]),
        "edl_created": STATE["edl_created"],
        "clips": len(clips),
        "analysed": len(clips) - len(pending),
        "pending": pending,
        "segments": len(read_edl().get("segments", [])),
        "proxies_ready": STATE.get("proxies_ready", False),
        "proxies": {"done": STATE.get("proxy_done", 0),
                    "total": STATE.get("proxy_total", 0),
                    "ready": STATE.get("proxies_ready", False)},
        "tools": {t: shutil.which(t) is not None
                  for t in ("ffmpeg", "ffprobe", "uv")},
        "backend": backend_preflight(),
        "visual": visual_status(),
    })


@app.put("/api/project")
async def api_save(request: Request) -> JSONResponse:
    body = await request.json()
    edl = read_edl()
    edl["story"] = body.get("story", edl.get("story", ""))
    clean = []
    for s in body["segments"]:
        seg = {k: s[k] for k in ("clip", "in", "out") if k in s}
        seg["in"] = round(float(seg["in"]), 2)
        seg["out"] = round(float(seg["out"]), 2)
        if seg["out"] <= seg["in"]:
            raise HTTPException(400, f"segment out <= in for {seg['clip']}")
        for k in ("act", "why"):
            if s.get(k):
                seg[k] = s[k]
        clean.append(seg)
    edl["segments"] = clean
    # Music is the same `effects_music` key assemble.py reads, validated the way a
    # segment is: an asset that is not in the library, or a gain outside range, is a 400
    # and nothing is written. A body that does not mention music leaves it alone; an
    # explicit null removes it.
    if "music" in body:
        music = body["music"]
        if music:
            try:
                spec = effects.music_spec(music)
                effects.resolve_asset(spec["asset"], STATE["assets"])
            except (ValueError, FileNotFoundError) as exc:
                raise HTTPException(400, f"music: {exc}")
            # gain_db stays unset unless given: the render measures the track and
            # sets the bed level from its loudness, which is the lesson of the first
            # inaudible bed.
            edl["effects_music"] = {k: v for k, v in spec.items() if v is not None}
        else:
            edl.pop("effects_music", None)
    # The bin learns from the timeline on every save: which keeps became shots, and
    # (once the bin lane lands) which shots were added by hand without a keep.
    if edl.get("selects") is not None or edl.get("floor") is not None:
        selects.sync_timeline(edl)
    write_edl(edl)
    return JSONResponse({"ok": True, "saved": len(clean)})


@app.post("/api/snap")
async def api_snap(request: Request) -> JSONResponse:
    """Run edl_snap.py over the current segments and hand back the result.

    Returned rather than written, so the UI can show it as a proposal the human
    accepts or undoes — a tool that silently rewrites the timeline is the opposite
    of fun.
    """
    body = await request.json()
    tmp_in = STATE["work"] / "snap_in.json"
    tmp_out = STATE["work"] / "snap_out.json"
    edl = read_edl()
    edl["segments"] = body["segments"]
    tmp_in.write_text(json.dumps(edl, indent=1), encoding="utf-8")
    # Off the event loop: `uv run` alone costs the better part of a second, and an
    # endpoint that blocks here stalls every other request — status, media, the
    # progress polls that exist to show the app is alive.
    r = await asyncio.to_thread(
        subprocess.run,
        ["uv", "run", "--quiet", str(TOOLS / "edl_snap.py"), str(tmp_in),
         "--sidecars", str(STATE["sidecars"]), "-o", str(tmp_out)],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise HTTPException(500, f"snap failed: {r.stderr[-300:]}")
    return JSONResponse({"segments": json.loads(tmp_out.read_text())["segments"],
                         "log": r.stdout})


def _ask_watcher(entry: progress.Job, shots: int):
    """Turn a half-written answer into a bar that moves.

    A `claude -p` call returns once, at the end. Streaming its deltas is what makes
    the difference between "thinking… 2:14" and "12 of ~18 shots decided": the plan is
    a JSON list of segments, so counting the ones written so far is real progress
    against a real denominator, and each phase boundary snaps the bar forward and
    re-times the rest from what this call has actually cost so far.
    """
    def on_partial(kind: str, text: str) -> None:
        if kind == "thinking":
            entry.complete("read")
            entry.note(f"working out the shape — {len(text) // 100 / 10:.1f}k "
                       "characters of reasoning" if len(text) > 400
                       else "working out the shape")
            return
        entry.complete("think")
        written = revise.count_shots(text)
        if '"notes"' in text:
            entry.complete("shots", detail="writing its reasoning for the editor")
            return
        entry.advance("shots", written / max(1, shots),
                      detail=f"{written} of ~{shots} shots decided")
    return on_partial


def _ask_job(job: str, segments: list[dict], clips: dict, story: str, note: str,
             target: tuple[float, float], ranked: list[dict] | None = None,
             focus: int | None = None) -> None:
    entry = ASKS[job]
    # Step one is not the edit, it is the estimate. Karl: *"the first step the AI must
    # complete is an estimate of how long it will take to apply the changes. This will
    # (when estimate is complete) start a progress bar."* It is one cheap call on the
    # per-unit role, it is bounded by its own short timeout, and it cannot fail in a
    # way that stops the work — a bad estimate falls back to a measured guess.
    #
    # A *shot-scoped* ask skips the estimate call: its prompt is one clip block rather
    # than a bin inventory, so it is a fraction of a full Ask, and the estimate is
    # arithmetic the app already knows — the same reasoning as analyse and render.
    if focus is not None:
        shots = 2
        est = revise.fallback_estimate(revise.SHOT_FALLBACK_ETA_S)
        entry.set_estimate(est.eta_s, est.milestones, source="measured",
                           detail="reading the clip")
        entry["estimate"] = {"source": "measured", "eta_s": est.eta_s,
                             "shots": shots, "why": "shot-scoped — computed",
                             "usage": {}}
    else:
        shots = revise.expected_shots(segments, target)
        est = revise.estimate_ask(clips, segments, note, target, shots=shots)
        entry.set_estimate(est.eta_s, est.milestones, source=est.source,
                           detail="reading the footage")
        entry["estimate"] = {"source": est.source, "eta_s": est.eta_s,
                             "shots": shots, "why": est.detail,
                             "usage": est.usage}
    entry["state"] = "running"
    try:
        watcher = _ask_watcher(entry, shots)
        if focus is not None:
            plan = revise.propose_shot(segments=segments, index=focus, clips=clips,
                                       story=story, note=note, target=target,
                                       on_partial=watcher)
            # The model answered for one shot; the proposal the human reads and
            # accepts is the whole timeline, so the splice happens here — a record on
            # disk holding only the replacement would offer "1 shot" as the recovered
            # cut and eat the film on accept.
            replaced = segments[focus]
            plan["focus"] = {"index": focus, "clip": replaced["clip"],
                             "in": replaced["in"], "out": replaced["out"],
                             "with": len(plan["segments"])}
            plan["segments"] = (segments[:focus] + plan["segments"]
                                + segments[focus + 1:])
        elif segments:
            plan = revise.propose(segments=segments, clips=clips, story=story,
                                  note=note, target=target, events=ranked,
                                  on_partial=watcher)
        else:
            plan = revise.originate(clips=clips, story=story, note=note,
                                    target=target, events=ranked,
                                    on_partial=watcher)
    except inference.BudgetExceeded as exc:
        entry.update(code=429)
        entry.finish("failed", detail=str(exc))
        return
    except (inference.InferenceError, ValueError) as exc:
        entry.update(code=502)
        entry.finish("failed", detail=str(exc))
        return
    entry.complete("notes", detail="snapping cut points to speech")
    # On disk before it is announced. A two-minute call whose only copy is an HTTP
    # response is one dropped connection away from being spent for nothing — which is
    # exactly what happened on the first Killington ask: the model answered, the
    # browser never showed it, and the plan was only recoverable from the CLI's own
    # session transcript.
    record = {"job": job, "created": time.time(), "note": note, "story": story,
              "plan": plan}
    path = STATE["asks"] / f"{job}.json"
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    entry["plan"] = plan
    entry.complete("polish")
    entry.finish("done", detail=f"{len(plan['segments'])} shots proposed")


@app.post("/api/ask")
async def api_ask(request: Request) -> JSONResponse:
    """Plain-language note in, timeline out — as a proposal, never a write.

    The one thing the board could not do before: let the human steer by *asking*
    rather than by dragging. Returned like /api/snap so the UI can show it, keep it
    undoable, and let the human reject it — a revision that applied itself would be
    the opposite of the "fun and easy" this app exists for.

    With an empty timeline the same call *originates* the cut instead of revising one.
    That is the piece that removes the hand-authored-EDL prerequisite, and it is
    deliberately not a separate button: "ask for what you want" should not change its
    name depending on whether there is anything on screen yet.
    """
    body = await request.json()
    note = (body.get("note") or "").strip()

    clips, payload = _ask_clips()
    target = payload["target"]
    segments = body.get("segments")
    if segments is None:
        segments = payload["segments"]
    story = body.get("story", payload.get("story", ""))
    if not note and segments:
        raise HTTPException(400, "empty note")
    if not clips:
        # Distinct from a model failure: nothing has been analysed, so there is
        # nothing to cut from. The UI can act on that; a 502 would just look broken.
        raise HTTPException(400, "no analysed clips yet — run the audio pass first")

    # A note about ONE shot: same loop, scoped call. Validated here so a stale index
    # is a 400 the UI can show, not a thread that dies estimating.
    focus = body.get("focus")
    if focus is not None:
        try:
            focus = int(focus)
        except (TypeError, ValueError):
            raise HTTPException(400, f"focus is not a shot index: {focus!r}")
        if not segments:
            raise HTTPException(400, "focus needs a cut to point into")
        if not (0 <= focus < len(segments)):
            raise HTTPException(400, f"no shot {focus + 1} in a "
                                     f"{len(segments)}-shot cut")
        if segments[focus]["clip"] not in clips:
            raise HTTPException(400, f"{segments[focus]['clip']} has no analysis")

    job = uuid.uuid4().hex[:8]
    if focus is not None:
        label = (f"Revising shot {focus + 1} — "
                 f"{Path(segments[focus]['clip']).stem}")
    else:
        label = "Revising the cut" if segments else "Building the first cut"
    ASKS[job] = progress.Job(
        "ask", label, id=job, state="estimating", plan=None, code=0,
        detail="estimating how long this will take",
        # `kind` was a human phrase here before the shared model gave the word a job
        # to do; the phrase is what the UI printed, so it survives under its own name.
        ask_kind="shot" if focus is not None
        else "revision" if segments else "first cut",
        estimate=None)
    threading.Thread(
        target=_ask_job,
        args=(job, segments, clips, story, note,
              (float(target[0]), float(target[1])), payload["events"], focus),
        daemon=True).start()
    return JSONResponse({"job": job})


@app.get("/api/ask/{job}")
def api_ask_status(job: str) -> JSONResponse:
    if job not in ASKS:
        raise HTTPException(404, "no such job")
    return JSONResponse(ASKS[job].snapshot())


@app.get("/api/asks/latest")
def api_ask_latest() -> JSONResponse:
    """The most recent proposal this project produced, from disk.

    So a reload, a closed tab or a dropped connection costs a click rather than
    another two-minute call.
    """
    files = sorted(STATE["asks"].glob("*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return JSONResponse({"record": None})
    return JSONResponse({"record": json.loads(files[0].read_text(encoding="utf-8"))})


# ---------------------------------------------------------------- find a moment


# What the bar assumes when a model search starts. The one live-shaped data point so
# far is the backend probe's ~7s round trip plus a small answer over a big prompt;
# recalibrated off the run in front of it like every other estimate.
FIND_ETA_S = 45.0
FIND_EXPECTED_MATCHES = 6


def _ask_clips() -> tuple[dict, dict]:
    """The clips dict the model-facing calls read, plus the full payload it came
    from. One shape, built one way — /api/ask grew its own copy of this inline and
    /api/find needing a second copy is what promoted it to a function."""
    payload = project_payload()
    clips = {c: {"clip": c, "duration": v["duration"], "transcript": v["transcript"],
                 "summary": v["summary"], "captured": v.get("captured"),
                 "visual": v.get("visual")}
             for c, v in payload["clips"].items()}
    return clips, payload


def _match_rows(matches: list[dict]) -> list[dict]:
    """A match, made playable: the row carries the full clip's proxy and a poster at
    the matched second, so the UI can show the moment and let the human scrub the
    whole clip around it — a window is somewhere to look, not yet a cut."""
    out = []
    for m in matches:
        stem = Path(m["clip"]).stem
        out.append({**m,
                    "proxy": f"/media/proxy/{stem}.mp4",
                    "poster": f"/media/poster/{stem}.jpg?t={m['start']:.2f}",
                    "duration": clip_duration(m["clip"])})
    return out


def _find_job(job: str, query: str, clips: dict, ranked: list[dict]) -> None:
    entry = FINDS[job]

    def on_partial(kind: str, text: str) -> None:
        if kind == "thinking":
            entry.complete("read")
            entry.note("working out where to look")
            return
        entry.complete("think")
        n = text.count('"clip"')
        entry.advance("write", n / FIND_EXPECTED_MATCHES,
                      detail=f"{n} match{'es' if n != 1 else ''} written")

    try:
        found = find.find(query, clips, ranked, on_partial=on_partial)
    except inference.BudgetExceeded as exc:
        entry.update(code=429)
        entry.finish("failed", detail=str(exc))
        return
    except (inference.InferenceError, ValueError) as exc:
        entry.update(code=502)
        entry.finish("failed", detail=str(exc))
        return
    entry["found"] = {"matches": _match_rows(found["matches"]),
                      "notes": found.get("notes", ""),
                      "usage": found.get("usage", {})}
    entry.complete("write")
    n = len(found["matches"])
    entry.finish("done", detail=f"{n} match{'es' if n != 1 else ''} found")


@app.post("/api/find")
async def api_find(request: Request) -> JSONResponse:
    """Find a moment in the bin from a plain-language description.

    Two layers, two prices. The word-level match over the transcripts and the visual
    sidecars is free and comes back in this response. `deep` starts one model call
    over the same inventory the Ask reads — for the misses word-matching cannot close
    (a "river" said as "stream") — and that is a job with a price, started only
    because the human pressed the button that carries it: the visual pass's rule.

    Either way the answer is windows to look at, never an edit: each match plays from
    the full clip and only becomes a segment when the human adds it.
    """
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "empty query — describe the moment")
    clips, payload = _ask_clips()
    if not clips:
        raise HTTPException(400, "no analysed clips yet — run the audio pass first")
    matches = find.lexical(query, clips)
    resp: dict = {"matches": _match_rows(matches), "job": None,
                  "deep_projected_usd": find.projected_usd(clips)}
    if body.get("deep"):
        running = next((f for f in FINDS.values()
                        if f["state"] not in progress.TERMINAL), None)
        if running is not None:
            raise HTTPException(409, "a model search is already running — "
                                     f"{running.get('detail') or running['label']}")
        job = uuid.uuid4().hex[:8]
        FINDS[job] = progress.Job(
            "find", f"Finding — {query[:48]}", id=job, state="running", found=None,
            code=0, query=query, detail="reading the bin")
        FINDS[job].set_estimate(
            FIND_ETA_S,
            [progress.milestone("read", "reading the bin", 0.15),
             progress.milestone("think", "working out where to look", 0.55),
             progress.milestone("write", "writing the matches", 0.30)],
            source="measured")
        threading.Thread(target=_find_job,
                         args=(job, query, clips, payload["events"]),
                         daemon=True).start()
        resp["job"] = job
    return JSONResponse(resp)


# ---------------------------------------------------------------- the floor
#
# The cutting room floor (docs/INTAKE.md): picks are derived from the sidecars and the
# events file on every read; the human's verdicts and notes live in the EDL as ranges on
# clip time (`selects`, `floor`). Nothing here spends a model call.


def released_clips() -> list[str]:
    """Clips the floor may show: listened to and previewable. The journal (INTAKE M3)
    will tighten this to "every stage done"; until then a clip with a sidecar and a
    proxy is releasable, because those are the two things a pick needs to play."""
    out = []
    for clip in footage_clips():
        stem = Path(clip).stem
        if stem in analysed_stems() and (STATE["proxy_dir"] / f"{stem}.mp4").exists():
            out.append(clip)
    return out


def _pick_rows(rows: list[dict]) -> list[dict]:
    """A pick, made playable: the full clip's proxy and a poster at the anchor."""
    out = []
    for p in rows:
        stem = Path(p["clip"]).stem
        out.append({**p, "proxy": f"/media/proxy/{stem}.mp4",
                    "poster": f"/media/poster/{stem}.jpg?t={p['anchor']:.2f}",
                    "duration": clip_duration(p["clip"])})
    return out


def _clip_range(body: dict) -> tuple[str, float, float]:
    clip = str(body.get("clip") or "")
    if clip not in {c for c in footage_clips()} and clip not in project_payload()["clips"]:
        raise HTTPException(400, f"unknown clip {clip!r}")
    try:
        start, end = float(body["start"]), float(body["end"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "start and end must be numbers")
    if not (0.0 <= start < end):
        raise HTTPException(400, f"{start}-{end} is not a range")
    return clip, start, end


@app.get("/api/picks")
def api_picks(order: str = "rank") -> JSONResponse:
    """Every pick in the bin with stored verdicts re-attached, best first or by clip."""
    if order not in ("rank", "clip"):
        raise HTTPException(400, f"unknown order {order!r}")
    # The full project payload, not the Ask's slimmer clip shape: picks need the R8
    # candidates, which the Ask never reads.
    payload = project_payload()
    edl = selects.ensure(read_edl())
    released = set(released_clips())
    themes = edl.get("themes") or []
    rows = picks.build(payload["clips"], payload["events"], themes=themes,
                       verdicts=edl["floor"]["verdicts"], selects=edl["selects"])
    for p in rows:
        p["released"] = p["clip"] in released
    ordered = picks.order(rows, order)
    return JSONResponse({
        "picks": _pick_rows(ordered),
        "rounds": len(picks.rounds([p for p in ordered if p["released"]])),
        "round_size": picks.ROUND_SIZE,
        "released": sorted(released),
        "summary": selects.summary(edl),
        "position": edl["floor"]["position"],
        "themes": themes,
        "dictation": dictate.available(),
    })


@app.post("/api/floor/verdict")
async def api_floor_verdict(request: Request) -> JSONResponse:
    """One verdict on one range: pick / reject / later / clear. Writes the EDL."""
    body = await request.json()
    clip, start, end = _clip_range(body)
    verdict = str(body.get("verdict") or "")
    edl = read_edl()
    try:
        selects.apply_verdict(
            edl, clip, start, end, verdict, why=str(body.get("why") or ""),
            note=str(body.get("note") or ""), hero=bool(body.get("hero")),
            witnesses=body.get("witnesses"), tags=body.get("tags"),
            source=str(body.get("source") or "floor"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    selects.used_in(edl)
    write_edl(edl)
    return JSONResponse({"ok": True, "summary": selects.summary(edl)})


@app.post("/api/floor/note")
async def api_floor_note(request: Request) -> JSONResponse:
    """A note on a range — the human's reason, typed or dictated."""
    body = await request.json()
    clip, start, end = _clip_range(body)
    edl = read_edl()
    selects.note(edl, clip, start, end, str(body.get("text") or ""))
    write_edl(edl)
    return JSONResponse({"ok": True, "summary": selects.summary(edl)})


@app.put("/api/floor/position")
async def api_floor_position(request: Request) -> JSONResponse:
    """Where the pass is, so quitting mid-round costs nothing."""
    body = await request.json()
    edl = selects.ensure(read_edl())
    pos = edl["floor"]["position"]
    for k in ("round", "index"):
        if k in body:
            try:
                pos[k] = max(0, int(body[k]))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} must be an integer")
    if "order" in body:
        if body["order"] not in ("rank", "clip"):
            raise HTTPException(400, f"unknown order {body['order']!r}")
        pos["order"] = body["order"]
    write_edl(edl)
    return JSONResponse({"ok": True, "position": pos})


@app.get("/api/selects")
def api_selects() -> JSONResponse:
    edl = selects.ensure(read_edl())
    selects.used_in(edl)
    return JSONResponse({"selects": edl["selects"], "floor": edl["floor"],
                         "summary": selects.summary(edl)})


@app.put("/api/selects")
async def api_selects_put(request: Request) -> JSONResponse:
    """Replace the whole bin — the bin editor's save. Validated like a plan."""
    body = await request.json()
    clips, _payload = _ask_clips()
    edl = selects.ensure(read_edl())
    try:
        edl["selects"] = selects.validate_selects(body.get("selects"), clips)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    selects.used_in(edl)
    write_edl(edl)
    return JSONResponse({"ok": True, "summary": selects.summary(edl)})


DICTATE_MAX_BYTES = 6 * 1024 * 1024        # ~30 s of webm/opus with room to spare


@app.post("/api/dictate")
async def api_dictate(request: Request) -> JSONResponse:
    """A spoken note in, its text out. The body is the recording itself (webm/opus or
    wav), not a form — no multipart dependency, and the floor posts a Blob directly."""
    if not dictate.available():
        raise HTTPException(501, "dictation is not built yet — see docs/INTAKE.md M4")
    raw = await request.body()
    if not raw:
        raise HTTPException(400, "empty recording")
    if len(raw) > DICTATE_MAX_BYTES:
        raise HTTPException(413, "recording too long — notes are at most 30 s")
    suffix = ".wav" if "wav" in (request.headers.get("content-type") or "") else ".webm"
    path = STATE["work"] / f"dictate_{uuid.uuid4().hex[:8]}{suffix}"
    path.write_bytes(raw)
    names = [t for t in (read_edl().get("names") or []) if isinstance(t, str)]
    try:
        result = await asyncio.to_thread(dictate.transcribe, path, names=names)
    except dictate.NotAvailable as exc:
        raise HTTPException(501, str(exc))
    finally:
        path.unlink(missing_ok=True)
    return JSONResponse(result)


@app.get("/floor", response_class=HTMLResponse)
def floor_page() -> HTMLResponse:
    return HTMLResponse((HERE / "static" / "floor.html").read_text(encoding="utf-8"),
                        headers=NO_STORE)


@app.get("/floor.js")
def floor_js() -> Response:
    p = HERE / "static" / "floor.js"
    if not p.exists():
        raise HTTPException(404, "the floor's script is not built yet")
    return Response(p.read_text(encoding="utf-8"),
                    media_type="application/javascript", headers=NO_STORE)


# ---------------------------------------------------------------- backend


BACKEND: dict = {"state": "unknown", "detail": "", "latency_ms": None}


def backend_preflight() -> dict:
    """What can be known about the backend *without* spending a call.

    Karl's report: backend and auth problems only surfaced ~80 seconds into an Ask,
    which is the worst possible moment to learn them. Most of the real failures are
    visible for free — a CLI that is not on PATH because the server was started from a
    non-login shell, an API backend with no key — so they are checked at launch and
    shown in the header rather than discovered mid-call.
    """
    name = config.backend_name()
    problems: list[str] = []
    if name == "claude_cli":
        if shutil.which("claude") is None:
            problems.append(
                "claude CLI not on PATH — it installs to ~/.local/bin, which a "
                "non-login shell does not pick up. Start from `bash -l`, or set "
                "ROUGHCUT_BACKEND=anthropic_api.")
    elif name == "anthropic_api":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            problems.append("ANTHROPIC_API_KEY is not set.")
    else:
        problems.append(f"unknown ROUGHCUT_BACKEND {name!r}")
    return {
        "backend": name,
        "model": config.model_for(config.ROLE_SKELETON),
        "problems": problems,
        "budget_usd": config.budget_usd(),
        "spent_usd": round(inference.spent_usd(), 4),
        **BACKEND,
    }


def _probe_job() -> None:
    """One deliberately tiny live call, to answer the question preflight cannot: is
    this backend actually authenticated?

    It runs on the cheap per-unit role rather than the skeleton role — it is proving
    the door opens, not that the big model is available — and it is one call, which
    matters more than its token count on a subscription where the scarce resource is
    requests per rolling window.
    """
    t0 = time.time()
    try:
        result = inference.complete("Reply with exactly: OK",
                                    role=config.ROLE_ANALYSIS)
    except inference.InferenceError as exc:
        BACKEND.update(state="failed", detail=str(exc)[:400],
                       latency_ms=int((time.time() - t0) * 1000))
        return
    BACKEND.update(state="ok", detail=str(result.content).strip()[:80],
                   latency_ms=result.latency_ms)


def probe_backend() -> None:
    if BACKEND["state"] == "checking":
        return
    BACKEND.update(state="checking", detail="", latency_ms=None)
    threading.Thread(target=_probe_job, daemon=True).start()


@app.post("/api/backend/probe")
def api_backend_probe() -> JSONResponse:
    probe_backend()
    return JSONResponse(backend_preflight())


# ---------------------------------------------------------------- analysis


def analyze_cmd(skip: list[str], force: bool) -> list[str]:
    """The audio pass, as a command. A function so the tests can replace it: running
    faster-whisper for real would mean a GPU, a 3GB model download and minutes per
    suite — none of which says anything about whether the job plumbing works."""
    cmd = ["uv", "run", "--quiet", str(TOOLS / "audio_analyze.py"),
           str(STATE["footage"]), "-o", str(STATE["sidecars"])]
    if skip:
        cmd += ["--skip", ",".join(skip)]
    if force:
        cmd += ["--force"]
    return cmd


PROGRESS_TICK_S = 2.0


def _ticker(stop: threading.Event, update) -> None:
    """Call `update` on a clock until told to stop.

    Progress must not depend on the child process saying anything. It used to: the
    counter was refreshed once per line of stdout, and `audio_analyze.py` prints
    without flushing, so a pipe holds all of it until exit — a 12-clip bin sat at 0/12
    for two and a half minutes and then jumped straight to done. The count was right
    and the trigger was wrong, which looks identical to a hung job.
    """
    while not stop.wait(PROGRESS_TICK_S):
        try:
            update()
        except OSError:
            pass          # a stat that fails must not kill the job it is watching


def _run_counted(entry: dict, cmd: list[str], count, key: str = "done",
                 append: bool = False, on_line=None, on_count=None) -> int:
    """Run a tool in the background, streaming its log into `entry` and ticking
    `entry[key]` from `count()` on a clock. Returns the exit code (-1: never ran).

    Counted from the sidecars on disk rather than parsed out of the tool's chatter: both
    passes write one file per clip as they finish, so the filesystem is the honest
    progress bar and stays right if the log format moves.

    `key` exists because the visual pass has two stages with different units — clips
    seen, then clips audited. Ticking both into `done` made a finished job report zero
    of three the moment the second stage started, which is worse than no progress bar.
    `append` keeps the earlier stage's log in front of this one's, so a job that failed
    in its second stage can still be diagnosed from its first.

    `on_line` and `on_count` are how the shared progress model reads this without
    changing what it does: the count still comes off the filesystem, and the two hooks
    turn it into a milestone's `part` and the tool's own chatter into the human line
    the top bar shows ("reading CLIP_04, sheet 2 of 3").
    """
    lines: list[str] = entry["log"].splitlines() if append and entry.get("log") else []
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            bufsize=1,
            # So the log streams too, rather than arriving in one lump at exit.
            env={**os.environ, "PYTHONUNBUFFERED": "1"})
    except OSError as exc:
        entry.update(state="failed", log=str(exc))
        return -1
    assert proc.stdout is not None

    def tick() -> None:
        entry[key] = count()
        if on_count is not None:
            on_count(entry[key])

    stop = threading.Event()
    ticker = threading.Thread(target=_ticker, args=(stop, tick), daemon=True)
    ticker.start()
    try:
        for line in proc.stdout:
            text = line.rstrip()
            lines.append(text)
            entry["log"] = "\n".join(lines[-40:])
            if on_line is not None:
                on_line(text)
        rc = proc.wait()
    finally:
        stop.set()
    tick()
    entry["log"] = "\n".join(lines[-40:])
    return rc


# Measured on Killington: 12 clips, 44 minutes of 5.3K. The ASR is about 12s of wall
# clock per minute of footage on this GPU, and the proxy encode about 40s per minute —
# which is why the two stages are weighted so unevenly and why parking the bar at 100%
# for the second one was the wrong answer.
ASR_S_PER_FOOTAGE_S = 0.20
PROXY_S_PER_FOOTAGE_S = 0.67


def _analyze_job(job: str, cmd: list[str], wanted: set[str]) -> None:
    entry = ANALYSES[job]
    rc = _run_counted(
        entry, cmd, lambda: len(wanted & analysed_stems()),
        on_count=lambda n: entry.advance(
            "listen", n / max(1, entry["total"]),
            detail=f"{n} of {entry['total']} clips transcribed"))
    if rc != 0:
        entry.finish("failed", detail="the audio pass failed — see the log")
        return
    entry.complete("listen")
    if entry["done"]:
        # Clips only become previewable once they have a proxy, and nothing else in
        # the app would build one for a clip that did not exist at launch. This runs
        # *before* the job reports done: a job that says "ready" while the previews
        # for its own clips are still being written hands the UI a broken <video>,
        # which stays broken until reload because the element does not retry.
        #
        # It is also the *longer* of the two stages — encoding 44 minutes of 5.3K
        # takes about half an hour, against two and a half minutes for the ASR — so it
        # reports its own count rather than leaving the bar parked at 100%.
        entry["stage"] = "previews"
        STATE["proxies_ready"] = False

        def previews(done: int, total: int) -> None:
            entry.update(proxy_done=done, proxy_total=total)
            entry.advance("previews", done / max(1, total),
                          detail=f"building preview {done} of {total}")

        ensure_proxies(
            sorted(c for c in footage_clips() if Path(c).stem in analysed_stems()),
            progress=previews)
    entry["stage"] = "done"
    entry.finish("done", detail=f"{entry['done']} clips analysed")


@app.post("/api/analyze")
async def api_analyze(request: Request) -> JSONResponse:
    """Run the audio pass over the bin, in-app.

    This was step 2 of five terminal steps standing between a folder of footage and
    the board. It is the cheapest of them to move inside — the tool already writes one
    sidecar per clip as it goes, so progress is real rather than a spinner.
    """
    body = await request.json()
    if any(a["state"] not in progress.TERMINAL for a in ANALYSES.values()):
        raise HTTPException(409, "an analysis is already running")
    skip = [str(s).strip() for s in body.get("skip", []) if str(s).strip()]
    force = bool(body.get("force"))
    skipped = {s.upper() for s in skip}
    wanted = {Path(c).stem for c in footage_clips()
              if Path(c).stem.upper() not in skipped}
    if not wanted:
        raise HTTPException(400, "no clips to analyse")

    job = uuid.uuid4().hex[:8]
    # Not a model call, so the estimate is arithmetic rather than a question: both
    # stages have a measured seconds-per-second of footage, and the footage is on disk.
    footage_s = sum(clip_duration(c) or 0.0 for c in footage_clips()
                    if Path(c).stem in wanted)
    ANALYSES[job] = progress.Job(
        "analyse", "Analysing the audio", id=job, stage="analysing", log="",
        total=len(wanted), done=len(wanted & analysed_stems()),
        proxy_done=0, proxy_total=0, detail="listening to the footage")
    ANALYSES[job].set_estimate(
        max(10.0, footage_s * (ASR_S_PER_FOOTAGE_S + PROXY_S_PER_FOOTAGE_S)),
        [progress.milestone("listen", "transcribing", ASR_S_PER_FOOTAGE_S),
         progress.milestone("previews", "building previews", PROXY_S_PER_FOOTAGE_S)],
        source="measured")
    threading.Thread(target=_analyze_job,
                     args=(job, analyze_cmd(skip, force), wanted),
                     daemon=True).start()
    return JSONResponse({"job": job, "total": len(wanted)})


@app.get("/api/analyze/{job}")
def api_analyze_status(job: str) -> JSONResponse:
    if job not in ANALYSES:
        raise HTTPException(404, "no such job")
    return JSONResponse(ANALYSES[job].snapshot())


# ---------------------------------------------------------------- the visual pass


def visual_cmd(only: list[str], force: bool) -> list[str]:
    """The visual pass, as a command. A function so the tests can replace it: the real
    tool spends a model call per contact sheet."""
    cmd = ["uv", "run", "--quiet", str(TOOLS / "visual_pass.py"), str(STATE["footage"]),
           "-o", str(STATE["visual"]), "--orient", STATE["orient"]]
    if only:
        cmd += ["--only", ",".join(only)]
    if force:
        cmd += ["--force"]
    return cmd


def scan_cmd(only: list[str], windows_out: Path, limit: int) -> list[str]:
    """The free motion scan, as a command — a function so the tests can replace it.
    Costs nothing but ffmpeg time, so it runs unconditionally before the close look."""
    cmd = ["uv", "run", "--quiet", str(TOOLS / "event_scan.py"), str(STATE["proxy_dir"]),
           "--sidecars", str(STATE["sidecars"]), "--visual", str(STATE["visual"]),
           "--limit", str(limit), "--windows-out", str(windows_out)]
    if only:
        cmd += ["--only", ",".join(only)]
    return cmd


def fine_cmd(windows: Path) -> list[str]:
    """The close look. Reads the **proxies**, not the masters: the thumbnails are
    480px either way, the proxy is already oriented, and seeking into a 5.3K HEVC
    master for an eight-second window costs more than the whole scan."""
    return ["uv", "run", "--quiet", str(TOOLS / "visual_pass.py"),
            str(STATE["proxy_dir"]), "-o", str(STATE["visual"]),
            "--interval", "1", "--cols", "3", "--rows", "5", "--width", "480",
            "--orient", "auto", "--windows", str(windows)]


def rebuild_events() -> int:
    """Re-derive the bin's ranked events file. Free — no model calls — so it runs after
    every pass rather than being something a human has to remember."""
    payload = events.build(STATE["visual"], STATE["sidecars"])
    events.write(STATE["visual"], payload)
    return len(payload["events"])


def _visual_job(job: str, cmd: list[str], wanted: set[str], fine: bool,
                windows_per_clip: int) -> None:
    """Coarse pass, then the free scan, then a close look at the busiest windows.

    Staged rather than merged because the stages have different prices and different
    failure modes: the coarse pass is what makes a clip "seen" at all, the scan is
    free, and the close look is the only one that can be skipped without leaving the
    board blind. A failed second stage keeps the first stage's work — every sheet of
    it was paid for.
    """
    entry = VISUALS[job]
    entry["stage"] = "looking"
    entry["state"] = "running"
    warn = ""
    rc = _run_counted(
        entry, cmd, lambda: len(wanted & visual_stems()),
        on_line=lambda line: _visual_note(entry, line),
        on_count=lambda n: entry.advance(
            "looking", n / max(1, entry["total"]),
            detail=_visual_note(entry)))
    if rc != 0:
        entry["stage"] = "looking"
        entry.finish("failed", detail="the visual pass failed — see the log")
        return
    entry.complete("looking")
    if fine:
        entry["stage"] = "scanning"
        entry["now"] = ""            # the coarse pass's last clip is not this stage's
        windows = STATE["work"] / f"windows_{job}.json"
        stems = sorted(Path(c).stem for c in footage_clips())
        entry.note("scanning for motion — free, no model calls")
        rc = _run_counted(entry, scan_cmd(stems, windows, windows_per_clip),
                          lambda: len(fine_stems()), key="fine_done", append=True)
        entry.complete("scan")
        if rc == 0 and windows.exists():
            entry["stage"] = "closer"
            total_fine = max(1, entry.get("fine_total") or len(stems))
            rc = _run_counted(
                entry, fine_cmd(windows), lambda: len(fine_stems()),
                key="fine_done", append=True,
                on_line=lambda line: _visual_note(entry, line, key="fine_done",
                                                  total=total_fine,
                                                  verb="looked closely at"),
                on_count=lambda n: entry.advance(
                    "closer", n / total_fine,
                    detail=_visual_note(entry, key="fine_done", total=total_fine,
                                        verb="looked closely at")))
        if rc != 0:
            # Not a failure of the job: the clips have been seen, which is what the
            # board needs. The close look is an audit and it can be re-run for free.
            # It rides all the way to the final line rather than being overwritten by
            # it — a job that quietly dropped half its work and then reported "done,
            # 3 clips seen" is the shape of every bug this file's comments describe.
            warn = "the close look did not finish; coarse pass is kept"
        entry.complete("closer")
        windows.unlink(missing_ok=True)
    entry["stage"] = "ranking"
    entry.note("ranking what was seen")
    entry["events"] = rebuild_events()
    entry.complete("rank")
    entry["stage"] = "done"
    summary = f"{entry['done']} clips seen, {entry['events']} events ranked"
    entry.finish("done", detail=f"{summary} — {warn}" if warn else summary)


# `visual_pass.py` says what it is doing as it does it — "CLIP_04.MP4: 3 sheet(s)",
# then one line per sheet as each is paid for. That chatter is the only account of
# where a twenty-minute pass actually is, and it used to reach nothing but a log box
# nobody opens. Turned into the top bar's human line instead.
_SHEETS_RE = re.compile(r"^(\S+?):\s*(\d+) sheet")
_SHEET_DONE_RE = re.compile(r"^\s+(\S+?):\s*(\d+) moments")


def _visual_detail(line: str) -> str:
    m = _SHEETS_RE.match(line)
    if m:
        n = int(m.group(2))
        STATE["visual_now"] = {"clip": Path(m.group(1)).stem, "sheets": n, "done": 0}
        return f"reading {STATE['visual_now']['clip']} — {n} sheet{'' if n == 1 else 's'}"
    if _SHEET_DONE_RE.match(line):
        now = STATE.get("visual_now")
        if not now:
            return ""
        now["done"] += 1
        return (f"reading {now['clip']} — sheet {min(now['done'] + 1, now['sheets'])} "
                f"of {now['sheets']}")
    return ""


def _visual_note(entry: progress.Job, line: str | None = None, *, key: str = "done",
                 total: int | None = None, verb: str = "seen") -> str:
    """The one line the bar shows for a visual pass: the count, and what it is on.

    Composed rather than raced. The first live run had the two writers overwriting
    each other — the useful "reading CLIP_05 — 1 sheet" appeared for a second and was
    then replaced by "0 of 1 clips seen" by the two-second ticker, over and over. The
    count answers "how far", the log line answers "on what", and a bar needs both.
    """
    if line is not None:
        got = _visual_detail(line)
        if got:
            entry["now"] = got
    done = entry.get(key) or 0
    of = entry["total"] if total is None else total
    text = f"{done} of {of} clip{'' if of == 1 else 's'} {verb}"
    if entry.get("now"):
        text = f"{text} — {entry['now']}"
    entry.note(text)
    return text


@app.post("/api/visual")
async def api_visual(request: Request) -> JSONResponse:
    """Look at the footage — the visual pass, in-app.

    Karl, on the first Killington cut: *"the analysis missed some critical moments that
    would have required video analysis — like me falling into a river."* The pass that
    finds those exists (research/tools/visual_pass.py) but lived in a terminal; this runs
    it over the bin with the same honest progress as the audio pass, counted from the
    sidecars it writes. It costs model calls, so it is never started on the app's own
    initiative — the status carries the price and the board asks.
    """
    body = await request.json()
    if any(v["state"] not in progress.TERMINAL for v in VISUALS.values()):
        raise HTTPException(409, "a visual pass is already running")
    only = {str(s).strip().upper() for s in body.get("only", []) if str(s).strip()}
    force = bool(body.get("force"))
    # The close look is on by default and cheap by default: three windows per clip,
    # priced in /api/status alongside the coarse pass so the button carries the total.
    fine = bool(body.get("fine", True))
    windows_per_clip = max(1, min(8, int(body.get("fine_windows",
                                                  FINE_WINDOWS_PER_CLIP))))
    done = visual_stems()
    wanted = {Path(c).stem for c in footage_clips()
              if (not only or Path(c).stem.upper() in only)
              and (force or Path(c).stem not in done)}
    if not wanted:
        raise HTTPException(400, "nothing to look at — every clip has been seen")

    job = uuid.uuid4().hex[:8]
    clips = footage_clips()
    sheets = sum(max(1, math.ceil((clip_duration(c) or 0.0) / VISUAL_SHEET_S))
                 for c in clips if Path(c).stem in wanted)
    fine_clips = len([c for c in clips if Path(c).stem not in fine_stems()]) if fine \
        else 0
    VISUALS[job] = progress.Job(
        "visual", "Looking at the footage", id=job, stage="looking", log="",
        total=len(wanted), done=0, fine_done=0, fine_total=max(1, fine_clips),
        events=0, fine=fine, sheets=sheets, now="",
        detail=f"{sheets} contact sheet{'' if sheets == 1 else 's'} to read")
    # The estimate here is arithmetic, not a question: the sheet count comes off the
    # footage on disk and the per-sheet cost and latency are measured (VISUAL_USD_PER_
    # SHEET, and ~40s a sheet on this machine). Asking a model to guess a number the
    # app can compute would cost a call to be *less* accurate — the estimate exists to
    # be right, not to be a ritual. The Ask, whose length genuinely cannot be computed,
    # is where the model estimate earns its call.
    milestones = [progress.milestone("looking", "reading contact sheets",
                                     max(1.0, sheets * VISUAL_SHEET_READ_S))]
    eta = max(20.0, sheets * VISUAL_SHEET_READ_S)
    if fine:
        milestones.append(progress.milestone("scan", "scanning for motion",
                                             max(1.0, len(clips) * SCAN_S_PER_CLIP)))
        milestones.append(progress.milestone(
            "closer", "a closer look",
            max(1.0, fine_clips * windows_per_clip * FINE_WINDOW_READ_S)))
        eta += len(clips) * SCAN_S_PER_CLIP
        eta += fine_clips * windows_per_clip * FINE_WINDOW_READ_S
    milestones.append(progress.milestone("rank", "ranking what was seen", 2.0))
    VISUALS[job].set_estimate(eta + 2.0, milestones, source="measured")
    threading.Thread(target=_visual_job,
                     args=(job, visual_cmd(sorted(wanted), force), wanted, fine,
                           windows_per_clip),
                     daemon=True).start()
    return JSONResponse({"job": job, "total": len(wanted)})


@app.get("/api/visual/{job}")
def api_visual_status(job: str) -> JSONResponse:
    if job not in VISUALS:
        raise HTTPException(404, "no such job")
    return JSONResponse(VISUALS[job].snapshot())


def probe_duration(path: Path) -> float | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return round(float(r.stdout.strip().strip(",")), 2)
    except ValueError:
        return None


def probe_resolution(path: Path) -> tuple[int, int] | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True)
    try:
        w, h = r.stdout.strip().split(",")
        return int(w), int(h)
    except ValueError:
        return None


def _render_job(job: str, edl_path: Path, out_path: Path, meta: dict) -> None:
    entry = RENDERS[job]
    parts_dir = STATE["work"] / f"parts_{job}"
    cmd = ["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
           "--footage", str(STATE["footage"]), "--sidecars", str(STATE["sidecars"]),
           "--assets", str(STATE["assets"]), "--profile", meta.get("profile", "preview"),
           "--parts-dir", str(parts_dir), "-o", str(out_path)]

    # Same shape as the audio pass: a ticker counting finished parts on disk, so the
    # UI can say "cutting 7/16" instead of "rendering…" for two minutes. Karl, on
    # clicking Render: "got like no response - and just see rendering..."
    stop = threading.Event()

    def count() -> None:
        done = len(list(parts_dir.glob("part_*.mp4"))) if parts_dir.exists() else 0
        entry["done"] = done
        joining = done >= entry["total"]
        entry["stage"] = "joining" if joining else "cutting"
        if joining:
            # The old bar sat at 100% here, and joining a 4K delivery render is
            # minutes of it. A separate milestone means the bar stops at the share
            # cutting was worth and keeps counting the rest.
            entry.complete("cutting", detail=f"joining {entry['total']} shots")
        else:
            entry.advance("cutting", done / max(1, entry["total"]),
                          detail=f"cutting shot {done + 1} of {entry['total']}")

    ticker = threading.Thread(target=_ticker, args=(stop, count), daemon=True)
    ticker.start()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        stop.set()
        shutil.rmtree(parts_dir, ignore_errors=True)
    ok = r.returncode == 0
    if ok:
        # Written next to the file rather than kept in memory: renders outlive the
        # process, and a versions list that empties on restart is not a versions list.
        #
        # And written *before* the job reports done, because the UI refreshes the
        # versions list the moment it sees "done" — a render that announces itself
        # before its own metadata exists gets listed as an unlabelled older file.
        meta["duration_s"] = probe_duration(out_path)
        res = probe_resolution(out_path)
        if res:
            meta["width"], meta["height"] = res
        out_path.with_suffix(".json").write_text(json.dumps(meta, indent=1),
                                                 encoding="utf-8")
    entry.update(
        done=entry["total"] if ok else entry["done"],
        log=(r.stdout or "") + (r.stderr or ""),
        output=str(out_path) if ok else None,
        url=f"/media/render/{out_path.name}" if ok else None,
    )
    if not ok:
        entry["stage"] = "failed"
        entry.finish("failed", detail="the render failed — see the log")
        return

    # The review copy is the last phase of the render, not an errand after it. The
    # A/B players stream the copy and not the master, so a job that reports 100% the
    # moment ffmpeg exits is telling the truth about the encoder and a lie about the
    # thing being waited for — which is the whole complaint the top bar exists to
    # answer. Measured on this box for a 181 s cut: 39.7 s to derive from the 1080p
    # preview master, 95.4 s from the 4K delivery one.
    entry["stage"] = "review"
    entry.complete("joining", detail="making the copy the players stream")
    review = await_review(out_path, note=entry.note)
    entry["stage"] = "done"
    entry["review"] = review
    tail = {"ready": "", "failed": " — no review copy, the players use the master",
            "building": " — the review copy is still building"}[review]
    entry.finish("done",
                 detail=(f"{meta.get('duration_s') or 0:.0f}s of video, "
                         f"{meta.get('profile', 'preview')}{tail}"))


# ------------------------------------------------------------ review copies

# A finished render is the master: `delivery` writes 4K at ~44 Mbps, and the one Karl
# watched is 987 MB for 3 minutes. A browser cannot play that off this box — measured,
# ffmpeg needs 92.8 s of wall clock to walk 181 s of it, half of real time — so the
# A/B players pointed at it stalled after a few seconds and then sat "loading forever".
# Karl: *"they seem to get stuck in this loading forever place and also only have
# played for like 3s before video buffers / pauses."* The file is not broken: frame
# intervals are a clean 1/29.97 throughout, 5427 of them. It is just heavy. So the
# players get a 720p copy — the same argument as the source proxies, one directory
# over — and the master stays untouched for the Download button.
REVIEW_SLOTS = threading.BoundedSemaphore(1)   # one at a time: a 4K master is minutes
REVIEW_STATE: dict[str, str] = {}              # render name -> building | ready | failed
REVIEW_LOCK = threading.Lock()


def review_path(name: str) -> Path:
    return STATE["reviews"] / Path(name).name


def build_review(src: Path, dest: Path) -> None:
    """A 720p, faststart copy of a finished render — same recipe as the proxies."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(f".{os.getpid()}.part.mp4")
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", str(src),
         "-map", "0:v:0", "-map", "0:a:0?", "-dn",
         # Capped by width, never upscaled: a preview render is already small and
         # re-encoding it bigger would spend bytes on detail that is not there.
         "-vf", f"scale=min({PROXY_W}\\,iw):-2", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", str(PROXY_CRF),
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)],
        capture_output=True, text=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"review copy failed for {src.name}: {r.stderr[-300:]}")
    tmp.replace(dest)


def claim_review(name: str) -> str | None:
    """Take responsibility for building this copy, or say who already has.

    `None` means the caller owns the build. Anything else is the state to report:
    a failure is remembered rather than retried on every poll — the UI falls back
    to the master and says so, and a restart tries again.
    """
    if review_path(name).exists():
        return "ready"
    with REVIEW_LOCK:
        known = REVIEW_STATE.get(name)
        if known in ("building", "failed"):
            return known
        REVIEW_STATE[name] = "building"
    return None


def make_review(src: Path, note: Callable[[str], None] | None = None) -> str:
    """Derive the copy on this thread, one at a time, and record how it went."""
    waited = not REVIEW_SLOTS.acquire(blocking=False)
    if waited and note:
        note("waiting for another review copy to finish")
    if waited:
        REVIEW_SLOTS.acquire()
    try:
        dest = review_path(src.name)
        state = "ready"
        if not dest.exists():
            try:
                t0 = time.time()
                if note:
                    note("making a 720p copy the players can stream")
                build_review(src, dest)
                print(f"review copy {src.name} in {time.time() - t0:.1f}s", flush=True)
            except RuntimeError as exc:
                print(f"  !! {exc}", flush=True)
                state = "failed"
        with REVIEW_LOCK:
            REVIEW_STATE[src.name] = state
        return state
    finally:
        REVIEW_SLOTS.release()


# How long a render waits on a copy somebody else is already building before it gives
# up and reports done anyway. Only reachable through a narrow race — a `/api/renders`
# poll landing between the master appearing on disk and the render reaching its review
# phase — and generous, because the thing it is waiting for is minutes of ffmpeg.
REVIEW_WAIT_S = 1800.0


def await_review(src: Path, note: Callable[[str], None] | None = None,
                 timeout: float = REVIEW_WAIT_S) -> str:
    """Don't come back until this render has a review copy, or definitively hasn't.

    Builds it here when nobody else is, waits on whoever is otherwise. Blocking is
    the point: the render job owns this phase, so the bar keeps moving through it.
    """
    claimed = claim_review(src.name)
    if claimed is None:
        return make_review(src, note)
    if claimed != "building":
        return claimed
    if note:
        note("waiting for the review copy already being made")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if review_path(src.name).exists():
            return "ready"
        with REVIEW_LOCK:
            state = REVIEW_STATE.get(src.name)
        if state in ("ready", "failed"):
            return state
        time.sleep(PROGRESS_TICK_S)
    return "building"


def ensure_review(src: Path) -> str:
    """Where the review copy of this render is up to, starting one if it is missing.

    The lazy path, for renders nothing is currently rendering: made before the copies
    existed, or left half-done by a restart. A render made *now* builds its own inside
    its job, so that the bar covers it.
    """
    claimed = claim_review(src.name)
    if claimed is not None:
        return claimed
    threading.Thread(target=make_review, args=(src,), daemon=True).start()
    return "building"


def download_name(meta: dict, path: Path, size: tuple[int, int] | None) -> str:
    """What the file should be called once it leaves the board.

    `cut_110ecb13.mp4` says nothing on a desktop full of downloads. Karl asked for
    this by name: *"Make it clear how to download the renders."*
    """
    bits = [STATE["footage"].name]
    if meta.get("segments"):
        bits.append(f"{meta['segments']}shots")
    dur = meta.get("duration_s")
    if dur:
        bits.append(f"{int(dur) // 60}m{int(dur) % 60:02d}")
    w, h = size or (meta.get("width"), meta.get("height"))
    if w and w >= 3840:
        bits.append("4K")
    elif h:
        bits.append(f"{h}p")
    else:
        bits.append(meta.get("profile", "preview"))
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", "-".join(str(b) for b in bits)).strip("-")
    return f"{stem or path.stem}.mp4"


RENDER_PROFILES = ("preview", "delivery")
# Seconds of wall clock per second of finished cut. Preview is measured: a 17-shot,
# 181s cut of the Killington bin rendered in 160.8s while an Ask and a second render
# shared the machine. Delivery comes from Karl's 17-minute render of a ~2:50 cut and is
# provisional — nothing here may start one to check. Both are starting guesses that the
# first milestone replaces with measurement off the run in front of it.
RENDER_S_PER_CUT_S = {"preview": 0.9, "delivery": 6.0}
# What share of that the join is worth. Measured at 5% on the preview above (7.9s of
# 160.8s); given more on delivery, where the join re-encodes at source resolution with
# the music mix rather than stream-copying 1080p parts. Provisional, and the reason the
# join is a milestone at all: the bar used to hit 100% the moment cutting ended.
RENDER_JOIN_SHARE = {"preview": 0.1, "delivery": 0.3}
# And what the review copy costs, in the same units, measured on this box against the
# 181s Killington cut with the box otherwise idle: 39.7s off the 1080p preview master
# and 95.4s off the 4K delivery one. It is a phase of the render because the players
# stream it — the render is not usable until it exists.
REVIEW_S_PER_CUT_S = {"preview": 0.22, "delivery": 0.53}


@app.post("/api/render")
async def api_render(request: Request) -> JSONResponse:
    body = await request.json()
    # One render at a time, the same rule the audio and visual passes already have.
    # Karl asked whether "hitting the button multiple times can break the system state
    # of the render": it cannot — every job has its own id, parts directory and output
    # file, so nothing is corrupted and nothing overwrites anything. What it does is
    # make you wait twice as long. His delivery render took ~17 minutes with the
    # machine otherwise idle; two 4K encodes compete for the same cores and both crawl.
    running = next((j for j in RENDERS.values()
                    if j["state"] not in progress.TERMINAL), None)
    if running is not None:
        raise HTTPException(
            409, f"render {running['id']} is already running — "
                 f"{running.get('detail') or running['label']}")
    profile = body.get("profile", "preview")
    if profile not in RENDER_PROFILES:
        raise HTTPException(400, f"unknown render profile: {profile!r}")
    edl = read_edl()
    edl["segments"] = body["segments"]
    job = uuid.uuid4().hex[:8]
    edl_path = STATE["work"] / f"render_{job}.json"
    edl_path.write_text(json.dumps(edl, indent=1), encoding="utf-8")
    out_path = STATE["renders"] / f"cut_{job}.mp4"
    meta = {
        "job": job, "created": time.time(), "title": edl.get("title", ""),
        "segments": len(edl["segments"]),
        "planned_s": round(sum(s["out"] - s["in"] for s in edl["segments"]), 2),
        "story": (edl.get("story") or "")[:300],
        "note": (body.get("label") or "")[:120],
        "music": (edl.get("effects_music") or {}).get("asset"),
        "profile": profile,
        # The shot list this file was made from, so the board can say which version is
        # the cut currently on the timeline. Karl watched a rendered *proposal* and
        # reported that the board "doesn't seem to reflect the render" — it did not,
        # and nothing on screen said which of the renders it did reflect.
        "shots": [{"clip": s["clip"], "in": s["in"], "out": s["out"]}
                  for s in edl["segments"]],
    }
    shots = len(edl["segments"])
    planned = meta["planned_s"]
    RENDERS[job] = progress.Job(
        "render", f"Rendering — {profile}", id=job, stage="cutting", log="",
        output=None, url=None, done=0, total=shots, profile=profile,
        planned_s=planned, detail=f"{shots} shots, {planned:.0f}s")
    # Measured on this machine: preview cuts run about 1.4x real time end to end and
    # a delivery render about 6x, with the join a fixed share of it. Not a promise —
    # it is recalibrated off the first milestone — but it is what makes the bar mean
    # something in the first thirty seconds, which is when Karl was looking at it.
    #
    # The weights are seconds-of-work per second of cut rather than fractions, so the
    # three phases keep their real proportions when the review copy is added: on a
    # preview render it is a fifth of the job, on a delivery render a twelfth.
    per_s = RENDER_S_PER_CUT_S[profile]
    join = RENDER_JOIN_SHARE[profile]
    review_s = REVIEW_S_PER_CUT_S[profile]
    RENDERS[job].set_estimate(
        max(5.0, planned * (per_s + review_s)),
        [progress.milestone("cutting", "cutting the shots", per_s * (1.0 - join)),
         progress.milestone("joining", "joining and mixing", per_s * join),
         progress.milestone("review", "making the review copy", review_s)],
        source="measured")
    threading.Thread(target=_render_job, args=(job, edl_path, out_path, meta),
                     daemon=True).start()
    return JSONResponse({"job": job})


@app.get("/api/renders")
def api_renders() -> JSONResponse:
    """Every cut rendered for this project, newest first.

    The board showed only the newest render, so comparing two versions meant finding
    mp4s on disk — and comparison is how you actually judge an edit. Reacting to a
    choice is faster and more informative than judging a single artifact.
    """
    out = []
    res_cache: dict = STATE.setdefault("render_res_cache", {})
    for mp4 in STATE["renders"].glob("cut_*.mp4"):
        meta_path = mp4.with_suffix(".json")
        meta = (json.loads(meta_path.read_text(encoding="utf-8"))
                if meta_path.exists() else {})
        size = mp4.stat().st_size
        # A row that offers a download has to say how big it is — 987 MB is worth
        # knowing before you click. Renders made before the profile existed carry no
        # dimensions, so they are probed once and remembered by path and mtime rather
        # than left blank: "don't know" reads as a broken file next to one that does.
        wh = (meta.get("width"), meta.get("height"))
        if not all(wh):
            key = (str(mp4), mp4.stat().st_mtime)
            if key not in res_cache:
                res_cache[key] = probe_resolution(mp4)
            wh = res_cache[key] or (None, None)
        out.append({
            "name": mp4.name, "url": f"/media/render/{mp4.name}",
            "size": size,
            "created": meta.get("created", mp4.stat().st_mtime),
            "duration_s": meta.get("duration_s"), "segments": meta.get("segments"),
            "planned_s": meta.get("planned_s"), "note": meta.get("note", ""),
            "music": meta.get("music"), "shots": meta.get("shots"),
            # Renders made before this profile existed carry no key — "preview"
            # is what they all were, and no width/height reads as "don't know",
            # never as "upscale to 4K".
            "profile": meta.get("profile", "preview"),
            "width": wh[0], "height": wh[1],
            # What the A/B players actually play, and how to get the master out of
            # the board. The review copy is derived in the background; until it is
            # there the list says so rather than handing a player a 987 MB file.
            "review_state": ensure_review(mp4),
            "review_url": f"/media/review/{mp4.name}",
            "download_url": f"/media/download/render/{mp4.name}",
            "download_name": download_name(meta, mp4, wh if all(wh) else None),
        })
    out.sort(key=lambda r: r["created"], reverse=True)
    return JSONResponse({"renders": out})


@app.get("/api/render/{job}")
def api_render_status(job: str) -> JSONResponse:
    if job not in RENDERS:
        raise HTTPException(404, "no such job")
    return JSONResponse(RENDERS[job].snapshot())


# ---------------------------------------------------------------- media


RANGE_CHUNK = 1 << 20            # 1 MiB


def iter_range(path: Path, start: int, end: int, chunk: int = RANGE_CHUNK):
    """Yield bytes `start`..`end` inclusive, a chunk at a time.

    The whole point is not to hold them. `fh.read(end - start + 1)` on the open-ended
    `bytes=0-` that every <video> sends first pulled an entire proxy into memory before
    a byte reached the browser — 85 MB for a Killington clip, and one page load of the
    16-shot cut fires sixteen of those plus two render previews of 150 MB each. Measured
    on the live board: the server went from 187 MB of RSS to 674 MB on a single load,
    with an all-time peak of 1.15 GB, for files it only ever had to copy.
    """
    remaining = end - start + 1
    with path.open("rb") as fh:
        fh.seek(start)
        while remaining > 0:
            buf = fh.read(min(chunk, remaining))
            if not buf:
                return
            remaining -= len(buf)
            yield buf


def parse_range(rng: str, size: int) -> tuple[int, int] | None:
    """One byte range against a file of `size`, or None if the header is not one.

    `bytes=-500` means the *last* 500 bytes, which the first version read as 0-500 —
    harmless while every proxy is written with `+faststart` and no player ever has to
    hunt for a trailing moov atom, and a silent wrong answer the day one is not.
    """
    m = re.fullmatch(r"\s*bytes=\s*(\d*)\s*-\s*(\d*)\s*", rng or "")
    if not m or not (m.group(1) or m.group(2)):
        return None                                   # not a single range: ignore it
    if not m.group(1):
        start, end = max(0, size - int(m.group(2))), size - 1
    else:
        start = int(m.group(1))
        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    return (start, end) if start <= end else None


def ranged_file(path: Path, request: Request) -> Response:
    """Serve a file with byte-range support.

    Required, not a nicety: without a 206 path the <video> element cannot seek, so
    every segment preview would have to stream from the start of the clip.
    """
    if not path.exists():
        raise HTTPException(404, f"not found: {path.name}")
    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    rng = request.headers.get("range")
    if not rng:
        return FileResponse(path, media_type=mime,
                            headers={"accept-ranges": "bytes"})
    span = parse_range(rng, size)
    if span is None:
        # An unparseable or unsatisfiable range: answer with the whole file rather than
        # a 416, which a media element treats as the file being broken.
        return FileResponse(path, media_type=mime,
                            headers={"accept-ranges": "bytes"})
    start, end = span
    return StreamingResponse(
        iter_range(path, start, end), status_code=206, media_type=mime, headers={
            "content-range": f"bytes {start}-{end}/{size}",
            "accept-ranges": "bytes",
            "content-length": str(end - start + 1),
        })


@app.get("/media/proxy/{name}")
def media_proxy(name: str, request: Request) -> Response:
    return ranged_file(STATE["proxy_dir"] / Path(name).name, request)


@app.get("/media/render/{name}")
def media_render(name: str, request: Request) -> Response:
    return ranged_file(STATE["renders"] / Path(name).name, request)


@app.get("/media/review/{name}")
def media_review(name: str, request: Request) -> Response:
    """The 720p copy of a render, which is what the A/B players play."""
    return ranged_file(review_path(name), request)


@app.get("/media/download/render/{name}")
def media_render_download(name: str) -> Response:
    """The master, named for a desktop rather than for a hash.

    Karl: *"Make it clear how to download the renders."* The board had no way to get
    a finished cut out of it other than knowing where `~/work/app/renders` is, and the
    one URL it did expose is served inline, so a click played it in a tab instead of
    saving it. `content-disposition: attachment` and a filename that says which bin,
    how many shots, how long and at what quality.
    """
    path = STATE["renders"] / Path(name).name
    if not path.exists():
        raise HTTPException(404, f"not found: {Path(name).name}")
    meta_path = path.with_suffix(".json")
    meta = (json.loads(meta_path.read_text(encoding="utf-8"))
            if meta_path.exists() else {})
    return FileResponse(path, media_type="video/mp4",
                        filename=download_name(meta, path, None))


# ---------------------------------------------------------------- posters

POSTER_W = 320               # a shot card's picture is 214 px wide; 320 covers a 2x screen
POSTER_Q = 5                 # mjpeg quality scale, 2 best .. 31 worst
# ffmpeg is cheap per frame and ruinous in bulk: one page load asks for a poster per
# card, and this box is often already encoding a delivery render. Three at a time keeps
# a cold board under a couple of seconds without taking the machine away from that.
POSTER_SLOTS = threading.BoundedSemaphore(3)
# Immutable by construction — a poster is one clip's frame at one timestamp, and the
# only way to change it is to ask for a different timestamp, which is a different URL.
POSTER_CACHE = {"cache-control": "public, max-age=31536000, immutable"}


def poster_path(stem: str, t: float) -> Path:
    return STATE["posters"] / f"{stem}@{t:09.2f}.jpg"


def build_poster(src: Path, dest: Path, t: float) -> None:
    """One frame out of a proxy, small, on disk.

    `-ss` ahead of `-i` so ffmpeg seeks to the timestamp instead of decoding its way
    there — 188 s into a Killington proxy is ~80 ms that way and seconds the other.
    Temp name and rename for the reason build_proxy has one: the server is serving
    while this runs, and two boards can share a work dir.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(f".{os.getpid()}.{threading.get_ident()}.part.jpg")
    err = ""
    # A trim can park an in-point past the end of a clip that was re-proxied shorter;
    # a card showing the first frame beats a card showing a broken image.
    for ss in dict.fromkeys((max(0.0, t), 0.0)):
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-nostdin", "-ss", f"{ss:.3f}",
             "-i", str(src), "-frames:v", "1",
             "-vf", f"scale=min({POSTER_W}\\,iw):-2", "-q:v", str(POSTER_Q),
             "-f", "image2", str(tmp)],
            capture_output=True, text=True)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size:
            tmp.replace(dest)
            return
        err = r.stderr[-200:]
        tmp.unlink(missing_ok=True)
    raise RuntimeError(f"poster failed for {src.name} at {t:.2f}s: {err}")


@app.get("/media/poster/{name}")
def media_poster(name: str, t: float = 0.0) -> Response:
    """A still frame from a proxy, at a shot's in-point.

    The shot cards used to be `<video preload="metadata">` elements pointed at the
    proxies. Sixteen of those plus two render previews is eighteen streams against
    Chrome's six-connections-per-host limit, and the monitor's own request queued
    behind them: measured on the Killington board from `playFrom(0)`, the live element
    took 6.8–9.5 s to reach readyState 4 and showed black (mean pixel 0.0) the whole
    time while the audio played — Karl's *"I can hear the videos... but the preview
    window still shows up blank"*. A card needs a picture, not a stream. This is a few
    kilobytes, cached on disk under --work and immutable for a given clip and time.
    """
    stem = Path(name).stem
    if not re.fullmatch(r"[A-Za-z0-9._-]+", stem):
        raise HTTPException(404, f"no such clip: {name}")
    src = STATE["proxy_dir"] / f"{stem}.mp4"
    if not src.exists():
        # Not an error the board should shout about: proxies build in the background,
        # and the cards retry when /api/status says they are done.
        raise HTTPException(404, f"no proxy yet: {stem}.mp4")
    if not math.isfinite(t) or t < 0:
        t = 0.0
    t = round(min(t, 86400.0), 2)
    dest = poster_path(stem, t)
    if not dest.exists():
        with POSTER_SLOTS:
            if not dest.exists():          # another request may have built it while we waited
                try:
                    build_poster(src, dest, t)
                except RuntimeError as exc:
                    raise HTTPException(500, str(exc)) from exc
    return FileResponse(dest, media_type="image/jpeg", headers=POSTER_CACHE)


# ---------------------------------------------------------------- assets

ASSET_KINDS = ("music", "sfx", "overlay")
ASSET_SUFFIXES = {
    "music": {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg"},
    "sfx": {".wav", ".mp3", ".m4a", ".flac"},
    "overlay": {".png"},
}


def list_assets() -> dict:
    """What is in the library, described. Tracks carry their measured loudness: a
    bed level is meaningless without it (the first bed was 25 dB under the film because a
    raw gain met a loud master). Measured once per file and cached by mtime."""
    cache: dict = STATE.setdefault("asset_cache", {})
    root: Path = STATE["assets"]
    out: dict = {k: [] for k in ASSET_KINDS}
    for kind in ASSET_KINDS:
        d = root / kind
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix.lower() not in ASSET_SUFFIXES[kind]:
                continue
            key = (str(p), p.stat().st_mtime)
            if key not in cache:
                info = {"name": p.name, "asset": f"{kind}/{p.name}", "kind": kind,
                        "url": f"/media/asset/{kind}/{p.name}", "size": p.stat().st_size}
                if kind in ("music", "sfx"):
                    info["duration_s"] = probe_duration(p)
                    lufs = effects.integrated_lufs(p)
                    info["lufs"] = round(lufs, 1) if lufs is not None else None
                cache[key] = info
            out[kind].append(cache[key])
    return out


@app.get("/api/assets")
def api_assets() -> JSONResponse:
    return JSONResponse(list_assets())


@app.get("/media/asset/{kind}/{name}")
def media_asset(kind: str, name: str, request: Request) -> Response:
    if kind not in ASSET_KINDS:
        raise HTTPException(404, f"no such asset kind: {kind}")
    return ranged_file(STATE["assets"] / kind / Path(name).name, request)


# The board's own code carried no cache headers at all: no Cache-Control, no ETag, no
# Last-Modified. Chrome refetches such a response — measured, on a reload it came back
# 200 from the network with no conditional headers — but that is a browser's choice and
# not a promise, and the page and the script are two files that have to agree with each
# other. An index.html holding a monitor the cached app.js has never heard of is a board
# that paints and does not play, which is exactly the report this came out of. They are
# 64 KB served over loopback; there is nothing to gain by caching them.
NO_STORE = {"cache-control": "no-store, must-revalidate"}


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((HERE / "static" / "index.html").read_text(encoding="utf-8"),
                        headers=NO_STORE)


@app.get("/app.js")
def appjs() -> Response:
    return Response((HERE / "static" / "app.js").read_text(encoding="utf-8"),
                    media_type="application/javascript", headers=NO_STORE)


def configure(edl: Path | None, footage: Path, sidecars: Path | None, work: Path,
              proxies: bool = True, orient: str = "auto",
              visual: Path | None = None, assets: Path | None = None) -> None:
    """Point the app at a project. Shared by main() and the test suite, so tests
    exercise the same wiring the server uses rather than a parallel setup.

    `edl` and `sidecars` may be None, in which case they default to derived paths
    under `work` and the EDL is scaffolded if it does not exist yet. That is the whole
    "new project" story: a footage folder is enough to open the board.
    """
    work = work.expanduser().resolve()
    footage = footage.expanduser().resolve()
    edl_path = (edl.resolve() if edl is not None
                else work / "projects" / f"{footage.name}.edl.json")
    created = not edl_path.exists()
    STATE.update({
        "edl": edl_path, "footage": footage,
        "sidecars": (sidecars.expanduser().resolve() if sidecars is not None
                     else work / "audio" / footage.name),
        "work": work,
        # Per-bin, because a footage folder is now something you point at rather than
        # a single configured project: two bins can hold the same GoPro stem, and a
        # shared proxy dir would serve one bin's frames for the other's clip.
        "proxy_dir": work / "proxies" / footage.name,
        # One JPEG per shot card, cut out of the proxy at the shot's in-point. Per-bin
        # like the proxies they come from, and disposable: every one of them is a
        # seek and a single frame, rebuilt on demand when the folder is not there.
        "posters": work / "posters" / footage.name,
        # Per-bin for the same reason as proxies, and because a fresh project that
        # opens claiming "1 version" and plays another trip's cut in the A slot is
        # worse than showing nothing.
        "renders": work / "renders" / footage.name,
        # 720p copies of the finished renders, for the A/B players. Per-bin like the
        # renders they come from, and as disposable as the posters: deleting the
        # folder costs one re-encode per version and nothing else.
        "reviews": work / "reviews" / footage.name,
        "proxies_ready": False, "edl_created": created,
    })
    STATE["sidecars"].mkdir(parents=True, exist_ok=True)
    if created:
        scaffold_edl(edl_path, footage, orient)
    STATE["orient"] = read_edl().get("orient", "auto")   # needs STATE["edl"] set first
    STATE["asks"] = work / "asks" / footage.name
    # Per-bin like proxies and renders — the third time this lesson has applied. The
    # earlier default of one shared ~/work/visual would have mixed two bins' sidecars
    # the moment their stems coincided.
    STATE["visual"] = (visual.expanduser().resolve() if visual is not None
                       else work / "visual" / footage.name)
    STATE["assets"] = (assets.expanduser().resolve() if assets is not None
                       else HERE.parent / "assets")
    STATE["work"].mkdir(parents=True, exist_ok=True)
    STATE["renders"].mkdir(parents=True, exist_ok=True)
    STATE["proxy_dir"].mkdir(parents=True, exist_ok=True)
    STATE["posters"].mkdir(parents=True, exist_ok=True)
    STATE["reviews"].mkdir(parents=True, exist_ok=True)
    # Keyed by render filename, so pointing the board at another bin must not carry a
    # previous project's "failed" over to a file of the same name.
    with REVIEW_LOCK:
        REVIEW_STATE.clear()
    STATE["asks"].mkdir(parents=True, exist_ok=True)

    clips = sorted({p.name.replace(".audio.json", ".MP4")
                    for p in STATE["sidecars"].glob("*.audio.json")})
    if not proxies:
        STATE["proxies_ready"] = True
    else:
        threading.Thread(target=ensure_proxies, args=(clips,), daemon=True).start()


def main() -> int:
    ap = argparse.ArgumentParser(description="Roughcut cut board.")
    ap.add_argument("--footage", type=Path, required=True,
                    help="folder of source clips — the only required argument")
    ap.add_argument("--edl", type=Path, default=None,
                    help="existing EDL; omitted, one is scaffolded under --work")
    ap.add_argument("--sidecars", type=Path, default=None,
                    help="audio sidecars; omitted, they live under --work per bin")
    ap.add_argument("--visual", type=Path, default=None,
                    help="visual_pass.py sidecars (default: per bin under --work). "
                         "Optional — the audio pass is local and cheap, this one "
                         "costs calls, so the board offers it with a price")
    ap.add_argument("--assets", type=Path, default=None,
                    help="asset library root for music/sfx/overlay (default: the "
                         "repo's assets/)")
    ap.add_argument("--orient", choices=("auto", "none"), default="auto",
                    help="rotation handling for a *new* project (per-bin, never "
                         "generalisable — see docs/HANDOFF.md)")
    ap.add_argument("--work", type=Path, default=Path.home() / "work" / "app")
    ap.add_argument("--port", type=int, default=87 * 100 + 65)   # 8765
    ap.add_argument("--no-proxies", action="store_true")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the one-call backend auth check at startup")
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe", "uv"):
        if shutil.which(tool) is None:
            raise SystemExit(f"error: {tool} not found on PATH")
    if not args.footage.expanduser().is_dir():
        raise SystemExit(f"error: no such footage folder: {args.footage}")

    configure(args.edl, args.footage, args.sidecars, args.work,
              proxies=not args.no_proxies, orient=args.orient, visual=args.visual,
              assets=args.assets)
    clips, done = footage_clips(), analysed_stems()
    # Every print here flushes: stdout to a pipe is block-buffered, so launch
    # diagnostics would otherwise sit unseen behind uvicorn.run for the life of the
    # process — which defeats the point of reporting problems at launch.
    print(f"{STATE['footage']}: {len(clips)} clips, "
          f"{sum(1 for c in clips if Path(c).stem in done)} analysed", flush=True)
    if STATE["edl_created"]:
        print(f"new project: {STATE['edl']}", flush=True)

    pre = backend_preflight()
    print(f"backend: {pre['backend']} · {pre['model']}", flush=True)
    for problem in pre["problems"]:
        print(f"  !! {problem}", flush=True)
    if not args.no_probe and not pre["problems"]:
        probe_backend()          # one call, in the background; result shows in the UI

    print(f"cut board on http://localhost:{args.port}  (edl: {STATE['edl'].name})", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
