# /// script
# requires-python = ">=3.12"
# dependencies = ["fastapi>=0.115", "uvicorn>=0.30", "numpy>=2", "pillow>=10"]
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
import base64
import json
import math
import mimetypes
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from fractions import Fraction
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, Response,
                               StreamingResponse)
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roughcut import (config, dictate, edits, effects, events, find, fx, inference,  # noqa: E402
                      journal, picks, progress, revise, selects, themes)
from roughcut import junk as junkmod  # noqa: E402
from roughcut import flow as flowmod  # noqa: E402
from roughcut import deep as deepmod  # noqa: E402
from roughcut import firstpass as fpmod  # noqa: E402
from roughcut import procscope  # noqa: E402
from roughcut import dirwatch  # noqa: E402
from roughcut import tracks as tracksmod  # noqa: E402
from roughcut import media as mediamod  # noqa: E402

# Stop means stop (verdict 3, punch 5): a job thread inside a `procscope.active` block
# starts its tools in their own process groups, and its cancel kills them.
procscope.install()

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
FX: dict[str, progress.Job] = {}          # effects: design, revise, verify (INTAKE M12)
DEEPS: dict[str, progress.Job] = {}       # look deeper at a span (INTAKE M15)


def all_jobs() -> list[progress.Job]:
    """Every long operation this server knows about, of any kind.

    Karl: *"consider a progress tracking bar up top for anything which may take time
    to complete — re-use across app."* One list is what makes that one bar possible,
    and it is what lets a reloaded page re-attach to a render and an Ask that are both
    still running — which is the state his machine is in as this is written.
    """
    return [*ANALYSES.values(), *VISUALS.values(), *INDEXES.values(), *ASKS.values(),
            *FINDS.values(), *THEMES.values(), *FX.values(), *RENDERS.values(),
            *DEEPS.values(), *IMPORTS.values()]


# Polled once a second by every open board, so it carries no payloads: a finished Ask
# holds a 15k-token plan and a render holds ffmpeg's whole log, and neither belongs in
# a heartbeat. Both stay one fetch away on /api/job/{id}.
JOB_LIST_OMIT = ("plan", "found", "journal", "proposal")


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
    for registry in (ANALYSES, VISUALS, INDEXES, ASKS, FINDS, THEMES, FX, RENDERS, DEEPS,
                     IMPORTS):
        if job in registry:
            return JSONResponse(registry[job].snapshot())
    raise HTTPException(404, "no such job")


# ---------------------------------------------------------------- colour (INTAKE M10)
#
# Per-clip colour files live under work/colour/<bin>/<stem>.colour.json — per bin like
# the proxies, because colour state is per project (Karl) and never carried over. They
# are measured from the proxy right after it is built (statistics do not need 4K), so
# there is no new journal stage: files are the truth, `measure_colour` is idempotent.

from roughcut import colour as colourmod

COLOUR_EVERY_S = 5.0


def colour_dir() -> Path:
    d = STATE.get("colour_dir") or (STATE["work"] / "colour" / STATE["footage"].name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def colour_file(clip: str) -> Path:
    return colour_dir() / f"{Path(clip).stem}.colour.json"


def clip_probe(clip: str) -> dict | None:
    """The colour tags of a source clip, cached per process: ffprobe once, not once
    per poll."""
    cache = STATE.setdefault("probes", {})
    if clip not in cache:
        src = clip_path(clip)
        try:
            cache[clip] = colourmod.probe(src) if src.exists() else None
        except RuntimeError:
            cache[clip] = None
    return cache[clip]


def measure_colour(clip: str, force: bool = False) -> dict | None:
    """Write (or read) the clip's colour file. Reads the proxy when it exists — it is
    SDR already, `build_proxy` normalised an HDR source — and the master otherwise,
    with the normalise applied on the way in. Never called from a GET (verdict 7
    blocker 3): the proxy jobs call it right after a proxy lands, and the colour queue
    (`queue_colour`) for whatever is still missing one."""
    with COLOUR_Q["lock"]:
        COLOUR_Q["measuring"][clip] = COLOUR_Q["measuring"].get(clip, 0) + 1
    try:
        return _measure_colour(clip, force)
    finally:
        with COLOUR_Q["lock"]:
            n = COLOUR_Q["measuring"].get(clip, 1) - 1
            if n <= 0:
                COLOUR_Q["measuring"].pop(clip, None)
            else:
                COLOUR_Q["measuring"][clip] = n


def _measure_colour(clip: str, force: bool = False) -> dict | None:
    out = colour_file(clip)
    if out.exists() and not force:
        try:
            return json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            pass
    p = clip_probe(clip)
    src = clip_path(clip)
    proxy = STATE["proxy_dir"] / f"{Path(clip).stem}.mp4"
    if proxy.exists():
        d = colourmod.measure_clip(proxy, COLOUR_EVERY_S, clip_probe=p, normalise=None)
    elif src.exists():
        d = colourmod.measure_clip(src, COLOUR_EVERY_S, clip_probe=p,
                                   normalise=colourmod.normalise_vf(p or {}))
    else:
        return None
    d["clip"] = clip
    tmp = out.with_suffix(f".{os.getpid()}.part.json")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    tmp.replace(out)
    return d


# The colour queue (verdict 7 blocker 3). A clip whose proxy has landed and whose colour
# file has not is measured here, one clip at a time, in a background scope: its ffmpeg
# runs at nice 19 and is SIGSTOPped while the Program plays (the YIELD encodes' rules),
# and it dies with the server. GET /api/junk only reads: it lists such a clip as
# `colour: "pending"` and starts this queue, and the board says "checking colour…".
COLOUR_Q: dict = {"lock": threading.Lock(), "pending": [], "measuring": {},
                  "failed": {}, "thread": None, "scope": None}


def colour_pending(clip: str) -> bool:
    """The clip has a proxy to measure and no colour file yet (and has not failed)."""
    if clip in COLOUR_Q["failed"]:
        return False
    return (not colour_file(clip).exists()
            and (STATE["proxy_dir"] / f"{Path(clip).stem}.mp4").exists())


def queue_colour(clips: list[str]) -> int:
    """Queue the clips for the background colour measurement; start the worker if it is
    not running. Returns how many are waiting or being measured."""
    with COLOUR_Q["lock"]:
        for c in clips:
            if c not in COLOUR_Q["pending"]:
                COLOUR_Q["pending"].append(c)
        n = len(COLOUR_Q["pending"]) + len(COLOUR_Q["measuring"])
        t = COLOUR_Q["thread"]
        if COLOUR_Q["pending"] and (t is None or not t.is_alive()):
            t = COLOUR_Q["thread"] = threading.Thread(target=_colour_loop, daemon=True,
                                                      name="colour")
            t.start()
    return n


def _colour_scope() -> procscope.Scope:
    with COLOUR_Q["lock"]:
        sc = COLOUR_Q["scope"]
        if sc is None or sc.cancelled:
            sc = COLOUR_Q["scope"] = procscope.Scope("colour", background=True)
        return sc


def _colour_loop() -> None:
    while True:
        scope = _colour_scope()
        with COLOUR_Q["lock"]:
            pending = COLOUR_Q["pending"]
            # a clip somebody else (the index's proxy stage) is measuring right now
            # waits for that measurement rather than running a second one
            clip = next((c for c in pending if c not in COLOUR_Q["measuring"]), None)
            if clip is None:
                if not pending:
                    COLOUR_Q["thread"] = None
                    return
        if clip is None:
            time.sleep(0.25)
            with COLOUR_Q["lock"]:
                COLOUR_Q["pending"] = [c for c in COLOUR_Q["pending"] if colour_pending(c)]
            continue
        try:
            if colour_pending(clip):
                with procscope.active(scope):
                    measure_colour(clip)
        except Exception as exc:  # noqa: BLE001 — one bad clip must not end the queue
            if not scope.cancelled:
                COLOUR_Q["failed"][clip] = str(exc)[:200]
                print(f"  !! colour: {clip}: {exc}", flush=True)
        finally:
            with COLOUR_Q["lock"]:
                if clip in COLOUR_Q["pending"]:
                    COLOUR_Q["pending"].remove(clip)
                if scope.cancelled:              # stopped (the server is going away)
                    COLOUR_Q["pending"].clear()
                    COLOUR_Q["thread"] = None
                    return


def load_colour(clip: str) -> dict | None:
    p = colour_file(clip)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def looks_library() -> dict[str, dict]:
    return colourmod.load_looks(STATE.get("assets"))


def resolve_colour(edl: dict) -> list[dict]:
    """Every shot's resolved colour, in film order, without the callable — what the
    inspector shows and what `/api/lut` bakes. The reference shot is the film's
    `colour.reference`, else the first shot; `match: previous` sees the shot before."""
    looks = looks_library()
    segs = edl.get("segments", [])
    ref_id = (edl.get("colour") or {}).get("reference")
    order = list(segs)
    if ref_id:
        order.sort(key=lambda s: 0 if s.get("id") == ref_id else 1)   # reference first
    resolved: dict[str, dict] = {}
    reference = previous = None
    for s in order:
        res = colourmod.resolve_shot(edl, s, load_colour(s["clip"]), looks,
                                     reference=reference, previous=previous)
        ctx = colourmod.shot_context(res)
        if reference is None and ctx:
            reference = ctx
        previous = ctx or previous
        resolved[s.get("id") or ""] = res
    out = []
    for s in segs:
        res = resolved.get(s.get("id") or "")
        if not res:
            continue
        summ = res.get("summary") or {}
        out.append({k: res[k] for k in ("id", "balance", "match", "look", "strength",
                                        "family", "identity")}
                   | {"clip": s["clip"],
                      "witness": {"white_source": summ.get("white_source"),
                                  "white": summ.get("white"), "clip": summ.get("clip"),
                                  "y_mid": summ.get("y_mid"), "chroma": summ.get("chroma"),
                                  "n": summ.get("n", 0)}})
    return out


def shot_fn(edl: dict, sid: str):
    """The composed mapping for one shot id, or None when the id is not in the cut."""
    looks = looks_library()
    segs = edl.get("segments", [])
    ref_id = (edl.get("colour") or {}).get("reference")
    reference = previous = None
    for s in sorted(segs, key=lambda s: 0 if ref_id and s.get("id") == ref_id else 1):
        res = colourmod.resolve_shot(edl, s, load_colour(s["clip"]), looks,
                                     reference=reference, previous=previous)
        if s.get("id") == sid:
            return res
        ctx = colourmod.shot_context(res)
        if reference is None and ctx:
            reference = ctx
        previous = ctx or previous
    return None


@app.get("/api/colour")
def api_colour() -> JSONResponse:
    edl = read_edl()
    ensure_segment_ids(edl)
    looks = looks_library()
    return JSONResponse({
        "film": edl.get("colour") or {},
        "looks": [{"name": k, "kind": v["kind"], "description": v.get("description", ""),
                   **({"error": v["error"]} if v.get("error") else {})}
                  for k, v in looks.items()],
        "shots": resolve_colour(edl),
        "clips": {c: {"measured": colour_file(c).exists(),
                      "family": (clip_probe(c) or {}).get("family"),
                      "hdr": (clip_probe(c) or {}).get("hdr")}
                  for c in footage_clips()},
    })


@app.get("/api/lut/{sid}")
def api_lut(sid: str, n: int = 17) -> JSONResponse:
    """The shot's LUT as a flat float table in .cube order (red fastest), for the
    monitor's WebGL shader. 17³ is within 0.1 dB of 33³ (the lab); the render bakes
    its own 33³ from the same function."""
    n = max(2, min(int(n), 33))
    edl = read_edl()
    ensure_segment_ids(edl)
    res = shot_fn(edl, sid)
    if res is None:
        raise HTTPException(404, f"no shot {sid}")
    table = colourmod.cube_table(res["fn"], n)
    return JSONResponse({"id": sid, "size": n, "identity": res["identity"],
                         "look": res["look"], "strength": res["strength"],
                         "balance": res["balance"],   # what was baked: the monitor previews the rest
                         "table":[round(float(v), 5) for v in table.reshape(-1)]})


# ---------------------------------------------------------------- proxies


# Seconds between proxy keyframes: what a cold seek decodes (up to this much of frames
# before the one it wants). 0.5 since v3 (verdict 6 punch 2, lane PLAY3): a park or a
# cue at 1 s decoded up to 30 frames of 1280 before showing one, 15 now.
PROXY_GOP_S = 0.5
# The proxy's version, in the proxy folder's name (configure): bump it whenever what a
# proxy is changes, so an old --work rebuilds its proxies rather than serving them.
# v2 (verdict 4 punch 5): a source above PROXY_MAX_FPS is proxied at a whole fraction
# of its rate — Killington's 59.94 GoPro clips at 29.97. The proxy is what the monitor
# plays and nothing else: the export, the clip facts and the frame grid all read the
# original. At 59.94 a loaded box played the monitor at ~7 frames a second around a cut;
# half the frames is half the decoding and half the grade's uploads.
# v3 (verdict 6 punch 2): a keyframe every 0.5 s (PROXY_GOP_S), not every 1 s.
# v4 (wave 8, lane YIELD; RANGEPROXY's request): on a GPU the whole proxy is decoded,
# scaled and encoded on the card (NVDEC → scale_cuda → NVENC, the range proxy's encoder
# settings) instead of a 4K/5.3K decode and x264 on the CPU — the same profile (1280
# wide, the rate rule, the orient rule, a keyframe every PROXY_GOP_S), another encoder.
PROXY_V = "v4"
PROXY_MAX_FPS = 30.5


def proxy_rate(src: Path) -> str | None:
    """The proxy's frame rate for `src` as an ffmpeg rational, or None to keep the
    source's: the source's own divided by the smallest whole number that brings it to
    PROXY_MAX_FPS or under (59.94 → 29.97, 50 → 25, 119.88 → 29.97)."""
    from fractions import Fraction
    fps = (media_facts(src) or {}).get("fps") or 0.0
    if fps <= PROXY_MAX_FPS:
        return None
    rate = Fraction(fps).limit_denominator(1001) / math.ceil(fps / PROXY_MAX_FPS)
    return f"{rate.numerator}/{rate.denominator}"


def proxy_gpu_args(src: Path, orient: str) -> list[str] | None:
    """The all-GPU proxy encode's arguments (between `-nostdin` and the output), or None
    when this source cannot take it: no NVENC here, an HDR source (its tone map is a CPU
    filter), not 8-bit 4:2:0, or a turn that is not a quarter. The profile is the CPU
    path's: PROXY_W wide at most (never up), `proxy_rate`'s rate, a keyframe every
    PROXY_GOP_S, AAC 128k. The orient rule is gpu-turn's (`build_preview`): autorotate does
    nothing to CUDA frames, so the matrix is overridden on the input and the turn is done
    explicitly on the 1280 frame after it comes down (the decode and the scale stay on the
    card); under `orient: none` it is overridden and nothing turns."""
    if not preview_gpu() or source_normalise(src):
        return None
    f = media_facts(src) or {}
    if f.get("pix_fmt") not in ("yuv420p", "yuvj420p", "nv12"):
        return None
    if not f.get("width") or not f.get("height"):
        return None
    turn = turn_needed(f, orient)
    if turn and turn not in TURN_VF:
        return None
    quarter = turn % 180 != 0
    ow, oh = (f["height"], f["width"]) if quarter else (f["width"], f["height"])
    w = min(PROXY_W, ow) // 2 * 2
    h = max(2, round(oh * w / ow / 2) * 2)
    sw, sh = (h, w) if quarter else (w, h)
    rate = proxy_rate(src)
    fps = float(Fraction(rate)) if rate else float(f.get("fps") or 30.0)
    gop = max(1, round(fps * PROXY_GOP_S))
    vf = f"scale_cuda={sw}:{sh}:format=nv12"
    if rate:
        vf = f"fps={rate},{vf}"            # first: the frames it drops are never scaled
    if turn:
        vf += f",hwdownload,format=nv12,{TURN_VF[turn]}"
    pre = ["-display_rotation", "0"] if (turn or orient == "none") else []
    return [*pre, "-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i", str(src),
            "-map", "0:v:0", "-map", "0:a:0", "-dn", "-vf", vf,
            "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0",
            "-g", str(gop), "-bf", "0",
            "-c:a", "aac", "-b:a", "128k", "-write_tmcd", "0", "-movflags", "+faststart"]


def build_proxy(src: Path, dest: Path, orient: str) -> str:
    """The 1280 proxy of `src` at `dest`; returns the path it took ("gpu" or "cpu")."""
    # Written to a temp name and renamed, because the server is already serving while
    # this thread runs: a half-written file at the final path is handed to a <video>
    # element as a truncated stream, which fails to decode and is then cached as
    # broken until a reload. os.replace is atomic within a filesystem.
    # Unique per process, not just per clip. Work dirs are shared by default now, so
    # two servers pointed at the same bin — easily done — would otherwise both write
    # the same .part path and hand the survivor a file interleaved from two encodes.
    tmp = dest.with_suffix(f".{os.getpid()}.part.mp4")
    gpu = proxy_gpu_args(src, orient)
    if gpu is not None:
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", *gpu, str(tmp)],
                           capture_output=True, text=True)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size:
            tmp.replace(dest)
            return "gpu"
        tmp.unlink(missing_ok=True)
        print(f"  proxy {src.name}: GPU path failed, CPU instead: {r.stderr[-200:]}",
              flush=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin"]
    if orient == "none":
        # Same lesson as assemble.py: -noautorotate leaves the display matrix on the
        # output, and a proxy that plays sideways in the UI is worse than no proxy.
        cmd += ["-display_rotation", "0"]
    # An HDR source (an iPhone shooting HLG by default) is tone mapped to SDR 709 on
    # the way into the proxy, from its own tags: an HLG proxy plays dark and washed in
    # the monitor, and every measurement downstream expects SDR (INTAKE M10).
    try:
        norm = colourmod.normalise_vf(colourmod.probe(src))
    except RuntimeError:
        norm = None
    vf = f"scale=min({PROXY_W}\\,iw):-2"
    if norm:
        vf = f"{norm},{vf}"
    rate = proxy_rate(src)
    if rate:
        vf = f"fps={rate},{vf}"       # first: the frames it drops are never converted
    cmd += [
        "-i", str(src), "-map", "0:v:0", "-map", "0:a:0", "-dn",
        # Capped by width so a source smaller than the target is left alone rather
        # than upscaled into a bigger file that carries no more detail.
        "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
        "-crf", str(PROXY_CRF),
        # A keyframe every PROXY_GOP_S, not x264's default of 250 frames (4 s at 60
        # fps): a seek decodes from the keyframe before it, and verdict 1 measured 1100
        # ms for a cold one against the rubric's 250 (punch 5).
        "-force_key_frames", f"expr:gte(t,n_forced*{PROXY_GOP_S:g})",
        "-c:a", "aac", "-b:a", "128k",
        "-write_tmcd", "0", "-movflags", "+faststart", str(tmp),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"proxy failed for {src.name}: {r.stderr[-300:]}")
    tmp.replace(dest)
    return "cpu"


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
    pending = list(todo)
    i = 0
    while pending:
        # The clip the human opened last goes next (punch 5): asked for by the monitor,
        # it is the one whose 720p matters now; the rest keep their order.
        clip = first_wanted(pending)
        pending.remove(clip)
        i += 1
        src = clip_path(clip)
        if not src.exists():
            print(f"  !! missing footage {clip}", flush=True)
            continue
        build_proxy(src, pdir / f"{Path(clip).stem}.mp4", STATE["orient"])
        # Its colour now, in this job (the caller's background scope: niced, paused
        # while the Program plays), not in the next GET /api/junk (verdict 7 blocker 3:
        # a reload after a proxy landed waited ~30 s on this measurement).
        try:
            measure_colour(clip, force=True)
        except RuntimeError as exc:
            print(f"  !! colour: {clip}: {exc}", flush=True)
        print(f"  [{i}/{len(todo)}] {clip}", flush=True)
        report(i)
    STATE["proxies_ready"] = True
    print("proxies ready", flush=True)
    # Colour is measured from the proxies once they exist — a couple of seconds per
    # clip at 720p — so a bin opened before M10 gets its colour files on first open.
    for clip in clips:
        if not colour_file(clip).exists():
            try:
                measure_colour(clip)
            except RuntimeError as exc:
                print(f"  !! colour: {clip}: {exc}", flush=True)


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


def folder_clips() -> list[str]:
    """The video files in the bin's own folder (`--footage`), by name."""
    footage: Path = STATE["footage"]
    if not footage.is_dir():
        return []
    return sorted(p.name for p in footage.iterdir()
                  if p.suffix.lower() in VIDEO_SUFFIXES)


def media_sources() -> mediamod.Sources:
    """The bin's imports and uploads (REDESIGN-GOAL dimension 13, `roughcut/media.py`):
    read once per bin (configure), kept in memory, written by this process only."""
    src = STATE.get("media_sources")
    if src is None or src.bin != STATE["footage"].name or src.dir.parent.parent != STATE["work"]:
        src = STATE["media_sources"] = mediamod.Sources(STATE["work"], STATE["footage"].name)
    return src


def footage_clips() -> list[str]:
    """Every clip of the bin: the folder's own files and what was imported or uploaded
    into it (names fixed at import, `roughcut/media.py`), whose files are there now.
    An imported clip keeps its name if a file with the same stem is dropped into the
    folder later; that newcomer is not listed (a clip's name is its identity: its
    sidecars, proxies and shots are keyed by it)."""
    own = folder_clips()
    extra = media_sources().present()
    if not extra:
        return own
    taken = {mediamod.stem_key(c) for c, _p in extra}
    return sorted([c for c in own if mediamod.stem_key(c) not in taken]
                  + [c for c, _p in extra])


def clip_path(clip: str) -> Path:
    """The file a clip name stands for: an import's or an upload's own path, else the
    bin's folder joined with the name. Every place that turns a clip name into a path
    goes through here (wave 12 IMPORT), so a clip imported from another card plays,
    probes, proxies and renders like one in the folder."""
    p = media_sources().path_of(str(clip))
    return p if p is not None else STATE["footage"] / clip


def tool_footage() -> Path:
    """The folder the tools are given (assemble.py's `--footage`, the audio and visual
    passes, telemetry): the bin's own folder while nothing is imported, else the bin's
    view under `--work` — one symlink per clip, named as the clip — because each tool
    joins clip names onto its one folder. Rebuilt per call (a listing, a few links)."""
    if not media_sources():
        return STATE["footage"]
    return mediamod.sync_view(mediamod.view_dir(STATE["work"], STATE["footage"].name),
                              {c: clip_path(c) for c in footage_clips()})


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
    src: Path = clip_path(clip)
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
    deep = deepmod.load(STATE["visual"], stem)
    if not p.exists() and not deep:
        return {}
    d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    fine = load_fine(stem)
    # The close look replaces the coarse account of the same seconds rather than
    # joining it. An inventory that still lists a backflip the 1s read found to be a
    # glove over the lens teaches the Ask exactly the wrong thing (R10). A deep look's
    # beats replace both for the seconds it read (INTAKE M15).
    return {"moments": events.merge_moments(d.get("moments", []), fine, deep),
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
VISUAL_INTERVAL_S = 4.0            # visual_pass.py's default: one frame every 4 s
VISUAL_INTERVALS = (4.0, 3.0, 2.0, 1.0)   # the open screen's slider, coarse to fine
VISUAL_CELLS = 30                  # 6 cols x 5 rows per sheet, the tool's default


def sheet_seconds(interval: float) -> float:
    """How much footage one contact sheet covers at a sample interval."""
    return VISUAL_CELLS * float(interval)


VISUAL_SHEET_S = sheet_seconds(VISUAL_INTERVAL_S)   # 120 s at the default
# Measured on the small tier (169 sheet calls in the ledger); scaled to whatever tier the
# coarse sheets run on now — the quick one, Karl 2026-10-03.
VISUAL_USD_PER_SHEET = round(
    0.09 * config.price_scale(config.model_for(config.ROLE_ANALYSIS)), 3)

# The second stage: a close look at the few busiest windows in each clip. Priced the
# same way and from the same kind of evidence — 23 fine sheets over three Killington
# clips came to $1.68, i.e. $0.073 each, barely under a coarse sheet despite covering
# eight seconds instead of two minutes, because the prompt and not the image is most of
# the input. Three windows per clip by default: enough to audit a clip's loudest
# claims, cheap enough that turning the stage on is not a decision.
FINE_WINDOWS_PER_CLIP = 3
# The close look is deep analysis, so it runs on the deep tier (`FINE_ROLE`); the $0.073
# was measured on the small tier and scales with it.
FINE_ROLE = config.ROLE_JUDGE
FINE_USD_PER_WINDOW = round(0.073 * config.price_scale(config.model_for(FINE_ROLE)), 3)

# The audit (R10's follow-up, HANDOFF roadmap item 1): the close look spent only on the
# coarse pass's own unaudited hot claims, bin-wide, in the order they rank while nobody
# has checked them. Twelve by default — Killington's top fifteen held eleven hot claims,
# none audited — at FINE_USD_PER_WINDOW each, so the button reads under a dollar.
AUDIT_CLAIMS = 12

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
        src: Path = clip_path(clip)
        d = probe_duration(src) if src.exists() else None
    cache[clip] = d
    return d


def on_clip_grid(d: float | None, fps) -> float | None:
    """A clip's length as whole frames of its own rate (verdict 7, punch 9).

    The sidecar's `duration_s` is the sound's length (CLIP_05: 35.2 s against 1057
    frames of picture), so a whole-clip drop on a 59.94 sequence ended at 35.1851, half
    a 29.97 frame — and the 29.97 export came out 16 ms shorter than the timeline said.
    The length the board is told is floor(d × fps) / fps; written up to the microsecond,
    so `out × fps` reads back as the whole count (never a hair under it). Unchanged when
    the rate is not known."""
    if d is None or fps is None:
        return d
    try:
        f = float(fps)
    except (TypeError, ValueError):
        return d
    if not 1.0 <= f <= 1000.0:
        return d
    r = float(Fraction(fps_rational(f)))
    n = math.floor(float(d) * r + 1e-6)
    x = round(n / r, 6)
    if x * r < n - 1e-7:
        x = round(x + 1e-6, 6)
    return x


def clip_rate(clip: str) -> float | None:
    """The clip's frame rate when its free facts are already known — never a probe, so
    nothing that serves the board waits on one (they are warmed at configure)."""
    return (STATE.get("clip_facts", {}).get(clip) or {}).get("fps")


def look_interval() -> float:
    """The sample interval the look pass runs at for this project — the open screen's one
    slider. Kept in the EDL (`look.interval_s`) because it is a property of the project,
    not of a run: a resumed index must look at the rest of the bin the way it looked at
    the first half."""
    try:
        v = float((read_edl().get("look") or {}).get("interval_s"))
    except (TypeError, ValueError):
        return VISUAL_INTERVAL_S
    return v if v in VISUAL_INTERVALS else VISUAL_INTERVAL_S


def set_look_interval(interval: float) -> None:
    edl = read_edl()
    look = dict(edl.get("look") or {})
    look["interval_s"] = float(interval)
    edl["look"] = look
    write_edl(edl)


def sheets_for(clip: str, interval: float | None = None) -> int:
    return max(1, math.ceil((clip_duration(clip) or 0.0)
                            / sheet_seconds(interval or look_interval())))


def audit_plan(ranked: list[dict], n: int = AUDIT_CLAIMS) -> list[dict]:
    """The windows an audit of the top `n` unaudited hot claims would buy.

    Planned off the ranked events rather than the sidecars: `unseen` there already means
    "no read window covers this claim to COVERED", and the order is the one the board
    shows, so the claims the button audits are the ones at the top of the seen tab.
    Claims close enough to share a window share it — `n` counts claims, the price counts
    windows.
    """
    claims = events.unaudited_claims(ranked)[:max(0, n)]
    return events.plan_claims(claims, limit=len(claims),
                              durations={c["clip"]: clip_duration(c["clip"])
                                         for c in claims})


def audit_price(plan: list[dict]) -> dict:
    return {"claims": sum(len(w["claims"]) for w in plan), "windows": len(plan),
            "calls": len(plan),
            "projected_usd": round(len(plan) * FINE_USD_PER_WINDOW, 2),
            "eta_s": round(len(plan) * FINE_WINDOW_READ_S + 2.0, 1),
            **config.cost_terms()}


def visual_status(fine: bool = True, interval: float | None = None) -> dict:
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
    interval = float(interval or look_interval())
    sheets = sum(sheets_for(c, interval) for c in pending)
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
        # The slider re-prices without a round trip: every interval's coarse price for
        # the same pending clips (the close look does not depend on it).
        "interval_s": interval,
        "intervals": list(VISUAL_INTERVALS),
        "by_interval": {f"{i:g}": round(sum(sheets_for(c, i) for c in pending)
                                        * VISUAL_USD_PER_SHEET
                                        + fine_calls * FINE_USD_PER_WINDOW, 2)
                        for i in VISUAL_INTERVALS},
        "running": any(v["state"] not in progress.TERMINAL for v in VISUALS.values()),
        "events": len(events.load(STATE["visual"])),
        # The audit button's price, read off the ranked file the board already shows.
        "audit": audit_price(audit_plan(events.load(STATE["visual"]))),
        "dir": str(STATE["visual"]),
        **config.cost_terms(),
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


def segment_id() -> str:
    return "g" + uuid.uuid4().hex[:10]


def ensure_segment_ids(edl: dict) -> bool:
    """Every segment carries a stable id (INTAKE M9 I9.0): minted once here for a cut
    written before ids existed, kept through every save after. Returns True when
    something was minted, so the caller can persist the migration."""
    changed = False
    for seg in edl.get("segments") or []:
        if not isinstance(seg.get("id"), str) or not seg["id"]:
            seg["id"] = segment_id()
            changed = True
    return changed


def project_payload() -> dict:
    edl = read_edl()
    ensure_segment_ids(edl)               # persisted at open (configure) and on every save;
    segments = [dict(s) for s in edl["segments"]]   # a read never writes

    clips: dict[str, dict] = {}
    referenced = {s["clip"] for s in segments}
    # Every clip with a sidecar is offered, not just the ones already used — adding a
    # shot you forgot is as much a part of fine-tuning as trimming one you have.
    for p in sorted(STATE["sidecars"].glob("*.audio.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        clip = d["clip"]
        rate = clip_rate(clip)
        clips[clip] = {
            "clip": clip,
            "stem": Path(clip).stem,
            # whole frames of the clip's own rate (verdict 7, punch 9), and that rate
            "duration": on_clip_grid(d["duration_s"], rate),
            "fps": rate,
            "proxy": f"/media/proxy/{Path(clip).stem}.mp4",
            # The card's picture: this URL plus ?t=<in-point>. A card is a still, not
            # a stream — see /media/poster for what sixteen streams cost the monitor.
            "poster": poster_url(Path(clip).stem),
            "transcript": d.get("transcript", []),
            "visual": load_visual(clip),
            "captured": capture_time(clip),
            "candidates": d.get("candidates", [])[:12],
            "summary": {k: d["summary"].get(k) for k in
                        ("speech_fraction", "wind_dominant_fraction",
                         "integrated_lufs", "audio_usable")},
            "used": clip in referenced,
        }
    # Generated clips (INTAKE M13): black, a colour, a freeze frame — real files the
    # server made on an apply, listed like any other clip so the monitor plays them
    # and an effect can be designed on one.
    clips.update(generated_clips(referenced))
    # A footage clip the editor put in the cut before the index heard it (redesign
    # wave 2, INGEST): listed with the free facts and an empty transcript, so the
    # monitor plays it and the Ask keeps it; the transcript arrives with the sidecar.
    for clip in sorted(referenced - clips.keys()):
        if not edits.is_generated(clip) and clip_path(clip).is_file():
            clips[clip] = unheard_clip(clip, used=True)
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
        "cut": cut_name_of(edl, STATE["edl"]),
        "music": edl.get("effects_music"),
        # The film's colour block (INTAKE M10); the per-shot resolution is /api/colour.
        "colour": edl.get("colour") or {},
        # The accepted effects (INTAKE M12); proposals are /api/fx.
        "effects": edl.get("effects") or [],
        # The tracks (REDESIGN-GOAL dimension 5, roughcut/tracks.py): the headers'
        # state in full, V2's clips, the sequence's markers and In / Out, and the
        # sequence rate the timecode counts in.
        **sequence_payload(edl, segments),
    }


def sequence_fps(segments: list[dict]) -> float:
    """The sequence rate: the most common frame rate among the footage clips the cut
    uses (the first clip in the bin when the cut is empty), as ffprobe reads it — cached
    per file version by `media_facts`. 23.976 when nothing can be probed."""
    clips = [s.get("clip") for s in segments if not edits.is_generated(str(s.get("clip")))]
    if not clips:
        clips = footage_clips()[:1]
    rates: dict[float, int] = {}
    for clip in dict.fromkeys(clips):
        src = clip_path(str(clip))
        facts = media_facts(src) if src.exists() else None
        r = (facts or {}).get("fps")
        if r:
            key = round(float(r), 3)
            rates[key] = rates.get(key, 0) + sum(1 for c in clips if c == clip)
    if not rates:
        return 23.976
    return max(rates.items(), key=lambda kv: (kv[1], kv[0]))[0]


def sequence_payload(edl: dict, segments: list[dict]) -> dict:
    overlays = [dict(o) for o in edl.get("overlays") or [] if isinstance(o, dict)]
    return {
        "tracks": tracksmod.resolve_tracks(edl.get("tracks")),
        "overlays": overlays,
        # the added audio tracks' clips (A3…, Sequence › Add Track)
        "audio": [dict(c) for c in edl.get("audio") or [] if isinstance(c, dict)],
        # transitions at the edits (verdict 6, punch 6): only those still on an edit
        "transitions": tracksmod.prune_transitions(edl.get("transitions"), segments),
        "markers": list(edl.get("markers") or []),
        "in": edl.get("in"),
        "out": edl.get("out"),
        "fps": sequence_fps(segments),
    }


@app.get("/api/project")
def api_project() -> JSONResponse:
    return JSONResponse(project_payload())


# The "building proxies" toast, once per pass (verdict 8, punch 7). The board kept the
# fact in the viewer's localStorage, and Killington's judge saw it on every page load for
# the hour the previews took: every fresh browser (a new profile, another machine, a
# private window, each of the judge's scripts) starts with no storage. The server is the
# one place that knows a pass, so it remembers here: a pass is this project's run of
# "previews not ready"; the first board to ask says it, every later ask in the same pass
# is told no. A pass is new when the project changed, or a clip is missing its proxy that
# was not missing when the pass was said (new footage); an ask that finds the previews
# ready ends the pass.
PROXY_TOAST: dict = {"key": None, "missing": frozenset()}
PROXY_TOAST_LOCK = threading.Lock()


@app.post("/api/proxies/toast")
def api_proxy_toast() -> JSONResponse:
    with PROXY_TOAST_LOCK:
        if STATE.get("proxies_ready", False):
            PROXY_TOAST.update(key=None, missing=frozenset())
            return JSONResponse({"say": False, "ready": True})
        key = str(STATE.get("edl"))
        pdir = STATE.get("proxy_dir")
        missing = frozenset(c for c in footage_clips()
                            if pdir is None or not (pdir / f"{Path(c).stem}.mp4").exists())
        same = PROXY_TOAST["key"] == key and missing <= PROXY_TOAST["missing"]
        if not same:
            PROXY_TOAST.update(key=key, missing=missing)
        return JSONResponse({"say": not same, "ready": False})


@app.get("/api/status")
async def api_status() -> JSONResponse:
    """Where this project actually is, in terms of the steps it has to go through.

    The board used to answer only "what is in the edit"; a human who had not run the
    terminal steps got an empty screen with nothing to explain it. This says how many
    clips exist, how many are analysed, and where the files are. From the snapshot
    (`_snapped`); the proxy walk's counters are merged in from memory."""
    return await snap_respond("status", status_payload, live=lambda: (
        STATE.get("proxies_ready"), STATE.get("proxy_done"), STATE.get("proxy_total")))


def status_payload() -> dict:
    out = dict(_snapped("status", _status_disk))
    out.update({
        "proxies_ready": STATE.get("proxies_ready", False),
        "proxies": {"done": STATE.get("proxy_done", 0),
                    "total": STATE.get("proxy_total", 0),
                    "ready": STATE.get("proxies_ready", False)},
    })
    return out


def _status_disk() -> dict:
    clips = footage_clips()
    done = analysed_stems()
    pending = [c for c in clips if Path(c).stem not in done]
    return ({
        "footage": str(STATE["footage"]),
        "footage_exists": STATE["footage"].is_dir(),
        "sidecars": str(STATE["sidecars"]),
        "edl": str(STATE["edl"]),
        "edl_created": STATE["edl_created"],
        "cut": cut_name_of(read_edl(), STATE["edl"]),
        "clips": len(clips),
        "analysed": len(clips) - len(pending),
        "pending": pending,
        "segments": len(read_edl().get("segments", [])),
        "tools": {t: shutil.which(t) is not None
                  for t in ("ffmpeg", "ffprobe", "uv")},
        "backend": backend_preflight(),
        "visual": visual_status(),
    })


def clean_segments(raw: list) -> list[dict]:
    """The segments a save (or an edits undo) is allowed to write: `clip`, `in`,
    `out` rounded, `act` / `why` when given, `speed` validated, the id kept when it is
    one. Raises HTTPException(400) with the sentence; nothing is written on one."""
    clean = []
    seen_ids: set[str] = set()
    for s in raw:
        seg = {k: s[k] for k in ("clip", "in", "out") if k in s}
        # 4 places, not 2: the timeline commits edit points on the sequence's frame grid
        # (a 29.97 frame is 0.0334 s), and 0.01 s rounding put them back off it (wave 3)
        seg["in"] = round(float(seg["in"]), 4)
        seg["out"] = round(float(seg["out"]), 4)
        if seg["out"] <= seg["in"]:
            raise HTTPException(400, f"segment out <= in for {seg['clip']}")
        for k in ("act", "why"):
            if s.get(k):
                seg[k] = s[k]
        # A gap on V1 (verdict 4, punch 9): empty film time, carried by a generated black
        # clip (POST /api/gap) so the monitor and the render play it as black and silence
        if s.get("gap") is True and edits.is_generated(seg["clip"]):
            seg["gap"] = True
        # Speed (INTAKE M13): kept only when it is not 1, validated the one way
        # `edits.validate_speed` validates it; a bad one is a 400 and nothing is written.
        try:
            speed = edits.validate_speed(s.get("speed"))
        except ValueError as exc:
            raise HTTPException(400, f"speed: {exc}")
        if speed is not None:
            seg["speed"] = speed
        # Linked A/V (roughcut/tracks.py): an unlinked pair and its A1 offset ride the
        # shot; in sync and linked is the absence of both keys.
        try:
            tracksmod.validate_link(s, seg)
        except ValueError as exc:
            raise HTTPException(400, f"{seg['clip']}: {exc}")
        # The id round-trips (I9.0): a shot keeps its identity across reorder, undo and a
        # proposal; a duplicate (a copied shot) or a missing one gets a fresh id.
        sid = s.get("id")
        if isinstance(sid, str) and 0 < len(sid) <= 40 and sid not in seen_ids:
            seg["id"] = sid
        else:
            seg["id"] = segment_id()
        seen_ids.add(seg["id"])
        clean.append(seg)
    return clean


def apply_sequence(edl: dict, body: dict) -> None:
    """The track model's keys of a save (roughcut/tracks.py), each validated; a bad one is
    a 400 and nothing is written. Absent leaves it alone; null (or an empty value) clears.
    Called before anything is written, so a 400 here writes nothing."""
    try:
        if "tracks" in body:
            spec = tracksmod.validate_tracks(body["tracks"])
            if spec:
                edl["tracks"] = spec
            else:
                edl.pop("tracks", None)
        if "overlays" in body:
            ovs = tracksmod.validate_overlays(body["overlays"], edits.validate_speed)
            for o, raw in zip(ovs, body["overlays"] or []):
                if not o["id"]:
                    o["id"] = segment_id()
                # a V2+ clip's own Effect Controls (verdict 6, punch 8), by V1's rule;
                # the render and the Program composite them over what is under it
                try:
                    c = clean_controls(raw.get("controls"), overlay=True)
                except TypeError as exc:
                    raise ValueError(f"overlay controls: {exc}")
                if c:
                    o["controls"] = c
            if ovs:
                edl["overlays"] = ovs
            else:
                edl.pop("overlays", None)
        if "audio" in body:
            clips = tracksmod.validate_audio_clips(body["audio"], edits.validate_speed)
            for c in clips:
                if not c["id"]:
                    c["id"] = segment_id()
            if clips:
                edl["audio"] = clips
            else:
                edl.pop("audio", None)
        if "markers" in body:
            marks = tracksmod.validate_markers(body["markers"])
            if marks:
                edl["markers"] = marks
            else:
                edl.pop("markers", None)
        for k in ("in", "out"):
            if k in body:
                v = tracksmod.validate_point(body[k], f"the sequence {k}")
                if v is None:
                    edl.pop(k, None)
                else:
                    edl[k] = v
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if edl.get("in") is not None and edl.get("out") is not None and edl["out"] <= edl["in"]:
        raise HTTPException(400, "the sequence out is not after its in")


# Effect Controls (redesign wave 2, lane EFFECTS): a shot's Motion, Opacity, Volume, the
# Lumetri values the colour block has no key for, and its fx stack (which designed
# effects are bypassed, and their order). They ride the segment as `controls`, holding
# only what differs from the default, so a shot keeps them through reorder, split and
# undo the way it keeps its speed. Ranges are Premiere's. Unknown keys are a 400.
CONTROL_RANGES = {
    "scale": (0.0, 1000.0, 100.0), "rotation": (-3600.0, 3600.0, 0.0),
    "opacity": (0.0, 100.0, 100.0), "volume_db": (-60.0, 6.0, 0.0),
    "contrast": (-100.0, 100.0, 0.0), "saturation": (0.0, 200.0, 100.0),
    # Lumetri Basic Correction's tone (wave 6): grade.js's shader documents the formula
    "highlights": (-100.0, 100.0, 0.0), "shadows": (-100.0, 100.0, 0.0),
    "whites": (-100.0, 100.0, 0.0), "blacks": (-100.0, 100.0, 0.0),
}

# Keyframes (wave 6, verdict 5 punch 4; schema in judge/LOOP.md): `controls.keys` is
# {prop: [[t, v], ...]} — t seconds from the shot's start in film time, strictly
# increasing, linear between keys and held outside them; v in the prop's range
# (position_x / position_y are fractions of the frame, as `position`). A prop with keys
# wins over its static value. At most KEYS_MAX keys per prop.
KEY_RANGES = {
    "position_x": (-10.0, 10.0), "position_y": (-10.0, 10.0),
    "scale": (0.0, 1000.0), "rotation": (-3600.0, 3600.0),
    "opacity": (0.0, 100.0), "volume_db": (-60.0, 6.0),
}
KEYS_MAX = 64
KEY_T_MAX = 24 * 3600.0


def _finite(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{what} must be a number")
    f = float(v)
    if not math.isfinite(f):
        raise ValueError(f"{what} must be finite")
    return f


def clean_keys(raw) -> dict | None:
    """A segment's `controls.keys`, validated; None when no prop has a key."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("keys must be an object of {prop: [[t, v], ...]}")
    extra = set(raw) - set(KEY_RANGES)
    if extra:
        raise ValueError(f"no keyframes for {sorted(extra)[0]!r}")
    out: dict = {}
    for prop, (lo, hi) in KEY_RANGES.items():
        lst = raw.get(prop)
        if lst is None:
            continue
        if not isinstance(lst, list):
            raise ValueError(f"keys.{prop} must be a list of [t, v]")
        if len(lst) > KEYS_MAX:
            raise ValueError(f"keys.{prop} has more than {KEYS_MAX} keys")
        nd = 6 if prop.startswith("position") else 3
        clean: list = []
        for k in lst:
            if not (isinstance(k, list) and len(k) == 2):
                raise ValueError(f"keys.{prop}: each key is [t, v]")
            t = _finite(k[0], f"keys.{prop} t")
            v = _finite(k[1], f"keys.{prop} value")
            if not (0.0 <= t <= KEY_T_MAX):
                raise ValueError(f"keys.{prop}: t must be 0..{KEY_T_MAX:g} s from the shot's start")
            if not (lo <= v <= hi):
                raise ValueError(f"keys.{prop}: value must be {lo:g}..{hi:g}")
            t = round(t, 6)
            if clean and t <= clean[-1][0]:
                raise ValueError(f"keys.{prop}: times must be sorted and distinct")
            clean.append([t, round(v, nd)])
        if clean:
            out[prop] = clean
    return out or None


# Lumetri beyond Basic (wave 11, LUMETRI): Curves, Color Wheels, HSL Secondary — the
# schema and THE formula are roughcut/colour.py's ("Lumetri beyond Basic"); each value's
# validator is there too. V1's only: a V2+ clip renders as shot (V2EC).
LUMETRI_CREATIVE = {"curves": colourmod.clean_curves, "wheels": colourmod.clean_wheels,
                    "hsl": colourmod.clean_hsl}


def clean_controls(raw, overlay: bool = False) -> dict | None:
    """One segment's `controls`, validated; None when everything is the default.
    `overlay`: a V2+ clip's, which takes no Lumetri Curves, Wheels or HSL."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("controls must be an object")
    extra = set(raw) - set(CONTROL_RANGES) - {"position", "fx_off", "fx_order", "keys"} \
        - set(LUMETRI_CREATIVE)
    if extra:
        raise ValueError(f"unknown control {sorted(extra)[0]!r}")
    if overlay and any(raw.get(k) is not None for k in LUMETRI_CREATIVE):
        raise ValueError("Lumetri Color applies to V1 shots: a clip on V2+ renders as shot")
    out: dict = {}
    for k, (lo, hi, default) in CONTROL_RANGES.items():
        if raw.get(k) is None:
            continue
        v = float(raw[k])
        if not (lo <= v <= hi):
            raise ValueError(f"{k} must be {lo:g}..{hi:g}")
        if round(v, 3) != default:
            out[k] = round(v, 3)
    pos = raw.get("position")
    if pos is not None:
        if not (isinstance(pos, list) and len(pos) == 2):
            raise ValueError("position must be [x, y], fractions of the frame")
        p = [round(float(v), 6) for v in pos]
        if not all(-10.0 <= v <= 10.0 for v in p):
            raise ValueError("position is outside -10..10 frames")
        if p != [0.5, 0.5]:
            out["position"] = p
    for k in ("fx_off", "fx_order"):
        ids = raw.get(k)
        if ids is None:
            continue
        if not (isinstance(ids, list) and all(isinstance(i, str) and 0 < len(i) <= 64 for i in ids)):
            raise ValueError(f"{k} must be a list of effect ids")
        if ids:
            out[k] = list(dict.fromkeys(ids))[:64]
    keys = clean_keys(raw.get("keys"))
    if keys:
        out["keys"] = keys
    for k, clean in LUMETRI_CREATIVE.items():
        v = clean(raw.get(k))
        if v:
            out[k] = v
    return out or None


def attach_controls(clean: list[dict], raw: list) -> None:
    """Carry each saved segment's validated `controls` onto its clean copy (same order)."""
    for seg, s in zip(clean, raw):
        try:
            c = clean_controls(s.get("controls") if isinstance(s, dict) else None)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"controls: {exc}")
        if c:
            seg["controls"] = c


def mint_board_ids(body: dict, memo: dict[str, str] | None = None) -> dict[str, str]:
    """Name the shots the board laid in since its last save (wave 5, lane SAVE).

    A shot made on the board (an insert, a split) carries a temporary `tmp-N` id until
    the server mints its real one. The board used to strip it and read the cut back to
    learn the new id by position, so a second save sent before that read minted a second
    id for the same shot, and anything the board had keyed by `tmp-N` (a per-shot grade)
    was written under a name no shot had. Now the board sends `tmp-N`; this mints one id
    per temporary id, renames the colour block's per-shot keys (and its reference) in the
    same body, drops a `tmp-` key no shot in the save carries, and returns the map, which
    the reply echoes so the board re-keys exactly. Rewrites `body` in place.

    `memo` (wave 8, SAVESAFE): the ids already minted for this page's `tmp-N`. A page
    that unloads while a save is in flight flushes its latest cut again (a keepalive
    PUT) carrying the same `tmp-N`, and both must name the shot alike."""
    ids: dict[str, str] = {}
    segs = body.get("segments")
    if isinstance(segs, list):
        for s in segs:
            sid = s.get("id") if isinstance(s, dict) else None
            if isinstance(sid, str) and sid.startswith("tmp-"):
                if sid not in ids:
                    ids[sid] = (memo.get(sid) if memo is not None else None) or segment_id()
                    if memo is not None:
                        memo[sid] = ids[sid]
                s["id"] = ids[sid]
    col = body.get("colour")
    if isinstance(col, dict):
        shots = col.get("shots")
        if isinstance(shots, dict):
            col["shots"] = {ids.get(k, k): v for k, v in shots.items()
                            if not (isinstance(k, str) and k.startswith("tmp-") and k not in ids)}
        ref = col.get("reference")
        if isinstance(ref, str) and ref.startswith("tmp-"):
            col["reference"] = ids.get(ref)
    return ids


# A save that never lands on a reload (wave 8, SAVESAFE; verdict 6 punch 1). The board
# tags each PUT `x-roughcut-save: <page>:<n>` (n counts up per page load) and, when the
# page is hidden or unloads with a save waiting or in flight, sends its latest cut once
# more as a keepalive PUT. That flush can land while an earlier PUT of the same page is
# still being processed (or is held up on its way), so a save older than one already
# applied for its page is never written over it: it is answered `stale` with the ids
# the newer one minted for its `tmp-N`. A PUT without the tag is applied as it comes.
SAVE_PAGES: dict[str, dict] = {}       # page -> {"seq": last applied n, "ids": {tmp: id}}
SAVE_PAGES_MAX = 64
# Test hook: hold every PUT /api/project this long after its body is read, as a stalled
# server would (test_save_reload.py; ROUGHCUT_TEST_PUT_DELAY_S for a subprocess server).
PUT_DELAY_S = float(os.environ.get("ROUGHCUT_TEST_PUT_DELAY_S") or 0.0)


def put_delay(request: Request) -> float:
    """The test hook's hold for this PUT (a test may hold only some of them)."""
    return PUT_DELAY_S


def save_tag(request: Request) -> tuple[str, int] | None:
    raw = request.headers.get("x-roughcut-save") or ""
    m = re.fullmatch(r"([A-Za-z0-9_-]{1,40}):(\d{1,12})", raw.strip())
    return (m.group(1), int(m.group(2))) if m else None


def save_page(page: str) -> dict:
    rec = SAVE_PAGES.pop(page, None) or {"seq": 0, "ids": {}}
    SAVE_PAGES[page] = rec                         # newest last
    while len(SAVE_PAGES) > SAVE_PAGES_MAX:
        SAVE_PAGES.pop(next(iter(SAVE_PAGES)))
    return rec


@app.put("/api/project")
async def api_save(request: Request) -> JSONResponse:
    body = await request.json()
    hold = put_delay(request)
    if hold > 0:
        await asyncio.sleep(hold)
    # From here to the reply nothing awaits: one save is applied whole before the next.
    tag = save_tag(request)
    page = save_page(tag[0]) if tag else None
    if page is not None and tag[1] <= page["seq"]:
        segs = body.get("segments") if isinstance(body.get("segments"), list) else []
        named = {s["id"]: page["ids"][s["id"]] for s in segs
                 if isinstance(s, dict) and s.get("id") in page["ids"]}
        return JSONResponse({"ok": True, "stale": True, "saved": len(segs), "ids": named})
    edl = read_edl()
    edl["story"] = body.get("story", edl.get("story", ""))
    minted = mint_board_ids(body, page["ids"] if page is not None else None)
    # A body without segments leaves the cut alone: the timeline saves its tracks,
    # V2 and markers on their own when nothing else changed (roughcut/tracks.py).
    if "segments" in body:
        clean = clean_segments(body["segments"])
        attach_controls(clean, body["segments"])   # Effect Controls (wave 2 EFFECTS)
        note_accept(edl, clean, body.get("colour"))   # provenance: before the segments go
        edl["segments"] = clean
    else:
        clean = edl.get("segments") or []
    apply_sequence(edl, body)
    # Transitions (verdict 6, punch 6) name their edit by its two shots, so they are
    # validated against the cut this save writes, their `tmp-N` ids renamed as the shots'
    # were; a save that changes the cut without sending them keeps those still on an edit.
    if "transitions" in body or "segments" in body:
        try:
            if "transitions" in body:
                named = {**(page["ids"] if page is not None else {}), **minted}
                trs = tracksmod.validate_transitions(body["transitions"], clean, named,
                                                     edits.dur)
            else:
                trs = tracksmod.prune_transitions(edl.get("transitions"), clean)
        except ValueError as exc:
            raise HTTPException(400, f"transitions: {exc}")
        if trs:
            edl["transitions"] = trs
        else:
            edl.pop("transitions", None)
    # Music is the same `effects_music` key assemble.py reads, validated the way a
    # segment is: an asset that is not in the library, or a gain outside range, is a 400
    # and nothing is written. A body that does not mention music leaves it alone; an
    # explicit null removes it.
    # Colour (INTAKE M10) is validated the same way: an unknown mode or look, an
    # override outside the vocabulary, is a 400 and nothing is written. Absent leaves
    # it alone; an explicit null or empty object removes it.
    if "colour" in body:
        try:
            spec = colourmod.validate_colour(body["colour"], looks_library())
        except ValueError as exc:
            raise HTTPException(400, f"colour: {exc}")
        if spec:
            edl["colour"] = spec
        else:
            edl.pop("colour", None)
    # Effects (INTAKE M12): every entry re-validated against the shots it names; a bad
    # one is a 400 and nothing is written. Absent leaves them alone.
    if "effects" in body:
        raw = body["effects"] or []
        if not isinstance(raw, list):
            raise HTTPException(400, "effects is not a list")
        try:
            edl["effects"] = [fx.validate_effect(e, edl.get("segments") or [])
                              for e in raw]
        except ValueError as exc:
            raise HTTPException(400, f"effects: {exc}")
    # Junk (HANDOFF item 5): the editor's verdicts by clip, validated like the rest; a
    # bad one is a 400 and nothing is written. Absent leaves them alone; null clears.
    if "junk" in body:
        try:
            spec = junkmod.validate(body["junk"])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if spec:
            edl["junk"] = spec
        else:
            edl.pop("junk", None)
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
    if page is not None:
        page["seq"] = tag[1]
    # A flush on unload carries the Accept's answer when the page could not wait for
    # this reply to send it (the answer goes after the cut, as the board's own does).
    if isinstance(body.get("answer"), dict):
        try:
            answer_ask(body["answer"])
        except (HTTPException, OSError, ValueError) as exc:
            print(f"  !! save: the answer it carried was not recorded: {exc}", flush=True)
    # `ids`: {tmp-N: minted id}, so the board re-keys its new shots from this reply
    return JSONResponse({"ok": True, "saved": len(clean), "ids": minted})


# Request timing (wave 8, SAVESAFE). Verdict 6 lost an accepted cut whose save "never
# replied" right after a first pass, and nothing said where the time went. Every request
# is timed to its first byte (the handler's work, not a video's streaming), any over
# SLOW_MS is printed with its handler and kept (the last 50, `GET /api/debug/slow`), and
# the slowest of all is remembered. A watcher notes the event loop itself held up for
# LOOP_LAG_MS or more (sync work in an async handler), the stall that delays every
# async handler at once.
SLOW_MS = 500.0
LOOP_LAG_MS = 250.0
SLOW_KEEP = 50
SLOW: list[dict] = []
SLOWEST: dict = {"all": None, "api": None}
_LOOP_WATCH: dict = {"task": None}
# Read your own save: a reload sends its keepalive flush and then asks for the page (and
# the cut) at once, on different connections; the page is served once the saves that
# had arrived are applied (waiting at most READ_AFTER_SAVE_S), never the cut before it.
SAVES_IN_FLIGHT = {"n": 0}
READ_AFTER_SAVE_S = 5.0


def note_request(row: dict) -> None:
    for key in ("all", "api") if row["path"].startswith("/api/") else ("all",):
        top = SLOWEST[key]
        if top is None or row["ms"] > top["ms"]:
            SLOWEST[key] = row
    if row["ms"] >= SLOW_MS:
        SLOW.append(row)
        del SLOW[:-SLOW_KEEP]
        print(f"  !! slow request: {row['ms']:.0f} ms {row['method']} {row['path']} "
              f"({row['handler']})", flush=True)


async def _loop_lag() -> None:
    tick = 0.1
    while True:
        t0 = time.perf_counter()
        await asyncio.sleep(tick)
        lag = (time.perf_counter() - t0 - tick) * 1000.0
        if lag >= LOOP_LAG_MS:
            row = {"method": "LOOP", "path": "(the event loop was held)", "handler": "-",
                   "ms": round(lag, 1), "status": None, "at": time.time()}
            SLOW.append(row)
            del SLOW[:-SLOW_KEEP]
            print(f"  !! the event loop was held {lag:.0f} ms", flush=True)


def _watch_loop() -> None:
    loop = asyncio.get_running_loop()
    task = _LOOP_WATCH["task"]
    if task is None or task.done() or task.get_loop() is not loop:
        _LOOP_WATCH["task"] = loop.create_task(_loop_lag())


class RequestTiming:
    """Pure ASGI (a BaseHTTPMiddleware would buffer the media streams)."""

    def __init__(self, app_) -> None:
        self.app = app_

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if (scope.get("client") or ("",))[0] != "testclient":   # a real server's loop
            _watch_loop()
        t0 = time.perf_counter()
        first: list = [None, None]
        method, where = scope.get("method"), scope.get("path")
        saving = method == "PUT" and where == "/api/project"

        async def timed(msg) -> None:
            if msg["type"] == "http.response.start" and first[0] is None:
                first[0], first[1] = time.perf_counter(), msg.get("status")
            await send(msg)

        if saving:
            SAVES_IN_FLIGHT["n"] += 1
        elif method == "GET" and where in ("/", "/api/project") and SAVES_IN_FLIGHT["n"]:
            until = time.perf_counter() + READ_AFTER_SAVE_S
            while SAVES_IN_FLIGHT["n"] and time.perf_counter() < until:
                await asyncio.sleep(0.02)
        try:
            await self.app(scope, receive, timed)
        finally:
            if saving:
                SAVES_IN_FLIGHT["n"] -= 1
            path = scope.get("path") or ""
            if path != "/api/debug/slow":
                end = first[0] if first[0] is not None else time.perf_counter()
                ep = scope.get("endpoint")
                note_request({"method": scope.get("method"), "path": path,
                              "handler": getattr(ep, "__name__", None) or "-",
                              "ms": round((end - t0) * 1000.0, 1), "status": first[1],
                              "at": time.time()})


app.add_middleware(RequestTiming)


@app.get("/api/debug/slow")
def api_debug_slow() -> JSONResponse:
    """Read-only: the last 50 requests over SLOW_MS (and event-loop holds), and the
    slowest request (`slowest`) and slowest `/api/` request (`slowest_api`) seen."""
    return JSONResponse({"threshold_ms": SLOW_MS, "slowest": SLOWEST["all"],
                         "slowest_api": SLOWEST["api"], "slow": list(SLOW)})


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
             focus: int | None = None, *, locked: list[str] | None = None,
             finish: Callable[[dict], dict] | None = None,
             originate: bool = False) -> None:
    """The Ask's work, on its thread. The first pass (`/api/firstpass`) runs the same
    call: `locked` marks the shots a re-run must return exactly, `finish` turns the plan
    into the re-run's proposal (locks enforced, the per-shot diff) before it is checked
    and saved, and `originate` asks for a first cut even when there is one."""
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
    grade = ask_colour_context(segments) if segments else None
    try:
        watcher = _ask_watcher(entry, shots)
        if focus is not None:
            plan = revise.propose_shot(segments=segments, index=focus, clips=clips,
                                       story=story, note=note, target=target,
                                       on_partial=watcher, colour=grade)
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
        elif segments and not originate:
            # The bin rides along: context for a revision, the contract for a first cut
            # (heroes must appear, keeps are bounds) — docs/INTAKE.md I1.1.
            plan = revise.propose(segments=segments, clips=clips, story=story,
                                  note=note, target=target, events=ranked,
                                  selects=read_edl().get("selects"),
                                  on_partial=watcher, colour=grade,
                                  **({"locked": set(locked)} if locked else {}))
        else:
            plan = revise.originate(clips=clips, story=story, note=note,
                                    target=target, events=ranked,
                                    selects=read_edl().get("selects"),
                                    on_partial=watcher)
    except inference.BudgetExceeded as exc:
        entry.update(code=429)
        entry.finish("failed", detail=str(exc))
        return
    except (inference.InferenceError, ValueError) as exc:
        entry.update(code=502)
        entry.finish("failed", detail=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — deliberately total
        # Anything else that escapes the call must still end the job. A thread that
        # dies with the job still `running` is a zombie: every board polling /api/jobs
        # sees an ask in flight forever and disables its own Ask button to match.
        entry.update(code=500)
        entry.finish("failed", detail=f"{type(exc).__name__}: {exc}"[:300])
        return
    entry.complete("notes", detail="snapping cut points to speech")
    # cut points on the sequence's frames, the cut's Effect Controls kept (the journey)
    plan = revise.to_sequence(plan, segments, sequence_fps(plan.get("segments") or segments))
    if finish is not None:
        plan = finish(plan)
    # The notes are held to the diff before anyone reads them (judge verdict 1, punch
    # 10): a "kept … unchanged" about a shot whose range moved is flagged and corrected.
    plan = revise.check_notes(plan, segments)
    if plan.get("colour"):
        # The words the proposal panel shows for the grade — computed here, where each
        # shot's resolved balance is known, so "warmer" is said against what the
        # monitor shows today rather than against a blank.
        rows = {r["id"]: r for r in (grade or {}).get("shots") or []}
        labels = {s["id"]: f"shot {i + 1} · {Path(s['clip']).stem}"
                  for i, s in enumerate(plan["segments"]) if s.get("id")}
        plan["colour_lines"] = colourmod.describe_patch(
            plan["colour"], labels, {k: v.get("balance") for k, v in rows.items()})
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
    entry.finish("done", detail="a colour change proposed, the cut as it is"
                 if plan.get("unchanged") else f"{len(plan['segments'])} shots proposed")


def ask_colour_context(segments: list[dict]) -> dict | None:
    """What the Ask is shown of the grade (INTAKE I10.5): the EDL's colour block, the
    looks library, and every shot of the cut being asked about resolved the way the
    inspector resolves it — balance, look, the witness numbers. Only shots with an id
    can be graded (overrides are keyed by id); a failure here costs the Ask its colour
    clause, never the Ask."""
    try:
        edl = read_edl()
        film = edl.get("colour") or {}
        keyed = [s for s in segments if isinstance(s.get("id"), str) and s.get("clip")]
        rows = resolve_colour({"segments": keyed, "colour": film}) if keyed else []
        return {"film": film, "looks": looks_library(), "shots": rows}
    except Exception as exc:  # noqa: BLE001 — the grade is optional to an Ask
        print(f"  !! ask colour context: {exc}", flush=True)
        return None


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

    clips, payload = _ask_clips(body.get("segments"), drop_junk=True)
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
    refuse_past_cap("this ask", ask_price("shot" if focus is not None else "full")["usd"])

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


# What an Ask costs, on its button before the click (INTAKE M16 I16.0f): fitted to the
# asks this bin already paid for, the way effects and deep looks are priced, and free.
# Each stored ask carries its call's tokens (`plan.usage`); they are re-priced at the
# deep role's model *now*, so a record made on one tier prices the next one honestly.
# With no record of the kind yet, a typical call measured on Killington stands in.
ASK_MODES = ("first", "bin", "full", "shot")
ASK_FIT_LAST = 5                       # the median of the newest few of a kind
ASK_TYPICAL_TOKENS = {                 # (in, out) of one call, Killington 2026-09/10:
    "cut": (42_000, 13_000),           # four whole-cut asks, 39–45k in, 9.5–15k out
    "shot": (28_500, 500),             # one shot ask, 28.5k in, ~0.5k out
}
ASK_ESTIMATE_TOKENS = (3_000, 400)     # the quick ETA call that opens a whole-cut ask


def ask_price(mode: str) -> dict:
    kind = "shot" if mode == "shot" else "cut"
    model = config.model_for(config.ROLE_SKELETON)
    fits: list[float] = []
    asks: Path = STATE["asks"]
    files = sorted(asks.glob("*.json"), key=lambda q: q.stat().st_mtime,
                   reverse=True) if asks.is_dir() else []
    for path in files:
        try:
            plan = json.loads(path.read_text(encoding="utf-8")).get("plan") or {}
            u = plan.get("usage") or {}
            tin, tout = int(u["input_tokens"]), int(u["output_tokens"])
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
        if ("focus" in plan) != (kind == "shot") or tin <= 0:
            continue
        fits.append(config.projected_usd(model, tin, tout))
        if len(fits) == ASK_FIT_LAST:
            break
    what = "shot asks" if kind == "shot" else "asks about the whole cut"
    if fits:
        fits.sort()
        mid = len(fits) // 2
        usd = fits[mid] if len(fits) % 2 else (fits[mid - 1] + fits[mid]) / 2
        basis = (f"the middle of this project's last {len(fits)} {what}, "
                 f"priced for {model}")
    else:
        usd = config.projected_usd(model, *ASK_TYPICAL_TOKENS[kind])
        basis = f"a typical one of the {what} — this project has none yet; priced for {model}"
    if kind == "cut":
        usd += config.projected_usd(config.model_for(config.ROLE_ANALYSIS),
                                    *ASK_ESTIMATE_TOKENS)
        basis += ", plus the quick estimate that draws its bar"
    return {"mode": mode, "usd": round(usd, 2), "basis": basis, "model": model,
            "fitted_on": len(fits), **config.cost_terms()}


@app.get("/api/ask/price")
def api_ask_price(mode: str = "full") -> JSONResponse:
    """The price on an Ask-family button (first cut, Cut from the bin, Ask, a shot's
    Ask) before it is pressed. Reads records; never calls the model. Declared before
    /api/ask/{job}, which would otherwise take "price" for a job id."""
    if mode not in ASK_MODES:
        raise HTTPException(400, f"mode must be one of {list(ASK_MODES)}")
    return JSONResponse(ask_price(mode))


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
        return JSONResponse({"record": None, "pending": False})
    record = json.loads(files[0].read_text(encoding="utf-8"))
    # Whether it still waits for an answer — the flow's own rule (I16.0 l): the board
    # offers "last proposal · show it" only then, never a 40-day-old answered one
    try:
        waiting = ask_pending(STATE["edl"].stat().st_mtime)
    except OSError:
        waiting = None
    return JSONResponse({"record": record,
                         "pending": bool(waiting) and waiting.get("job") == record.get("job")})


# ---------------------------------------------------------------- find a moment


# What the bar assumes when a model search starts. The one live-shaped data point so
# far is the backend probe's ~7s round trip plus a small answer over a big prompt;
# recalibrated off the run in front of it like every other estimate.
FIND_ETA_S = 45.0
FIND_EXPECTED_MATCHES = 6


def _ask_clips(segments: list[dict] | None = None, *,
               drop_junk: bool = False) -> tuple[dict, dict]:
    """The clips dict the model-facing calls read, plus the full payload it came
    from. One shape, built one way — /api/ask grew its own copy of this inline and
    /api/find needing a second copy is what promoted it to a function.

    `drop_junk` (the Ask and Find): clips the editor confirmed as junk leave the
    inventory — blocks of "(no speech)" over a black frame are tokens the model reads
    for nothing, and an originated cut that opens on one is the failure the junk pass
    exists for. A confirmed clip a shot of `segments` (default: the cut on disk) still
    uses stays: the human put it there, and the plan validator rejects a shot whose
    clip the inventory does not have."""
    payload = project_payload()
    clips = {c: {"clip": c, "duration": v["duration"], "transcript": v["transcript"],
                 "summary": v["summary"], "captured": v.get("captured"),
                 "visual": v.get("visual")}
             for c, v in payload["clips"].items()}
    if drop_junk:
        used = {s.get("clip") for s in (segments if segments is not None
                                        else payload["segments"]) if isinstance(s, dict)}
        for c in confirmed_junk() - used:
            clips.pop(c, None)
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


@app.get("/api/find/price")
def api_find_price() -> JSONResponse:
    """What *Ask the model* costs on this bin, before anything is typed (INTAKE I16.0f:
    every model button shows its price first). Free — the same `find.projected_usd`
    the POST returns, which reached the button only in the response to the click that
    had already started the call. `usd` is None while there is nothing to search."""
    clips, _ = _ask_clips(drop_junk=True)
    if not clips:
        return JSONResponse({"usd": None,
                             "why": "no analysed clips yet — run the audio pass first",
                             **config.cost_terms()})
    return JSONResponse({"usd": find.projected_usd(clips), **config.cost_terms()})


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
    clips, payload = _ask_clips(drop_junk=True)
    if not clips:
        raise HTTPException(400, "no analysed clips yet — run the audio pass first")
    matches = find.lexical(query, clips)
    resp: dict = {"matches": _match_rows(matches), "job": None,
                  "deep_projected_usd": find.projected_usd(clips), **config.cost_terms()}
    if body.get("deep"):
        running = next((f for f in FINDS.values()
                        if f["state"] not in progress.TERMINAL), None)
        if running is not None:
            raise HTTPException(409, "a model search is already running — "
                                     f"{running.get('detail') or running['label']}")
        refuse_past_cap("the model search", resp["deep_projected_usd"])
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
    """Clips the floor may show.

    With a journal (a bin indexed by POST /api/index) this is decision 3 exactly: every
    applicable stage done — the floor never shows a clip whose picture can't play or
    whose picks might still change. A bin indexed before journals existed has no
    journal, and for it a clip with a sidecar and a proxy is releasable, because those
    are the two things a pick needs to play.
    """
    if journal_path().exists():
        try:
            present = set(footage_clips())
            j = load_journal()
            if not j.paused_priced:
                return [c for c in j.released_clips() if c in present]
            # The budget cap pauses the priced stages; it must not take the floor away.
            # The design's own fallback is "picks from words and telemetry only", so
            # while paused a clip whose *free* stages are settled is released — its picks
            # come from the transcript and whatever coarse look it already has.
            return [c for c in j.ordered() if c in present
                    and not j.clips[c].get("missing")
                    and all(j.state(c, s) in journal.SETTLED
                            for s in journal.STAGES
                            if s not in journal.PRICED and s != "picks")]
        except journal.JournalError:
            pass                          # an unreadable journal must not blank the floor
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


def looked_at(clip: str) -> dict | None:
    """What the look pass sampled on this clip — the frames it actually read — so the
    pass can show how much of a clip was indexed by keyframe and which frames a claim
    rests on. None when the clip was never looked at. A sidecar written before the
    frames were recorded gets them re-derived from its interval and the duration."""
    stem = Path(clip).stem
    p: Path = STATE["visual"] / f"{stem}.visual.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    params = d.get("params") or {}
    interval = float(params.get("interval_s") or VISUAL_INTERVAL_S)
    frames = [float(f) for f in (d.get("frames_sampled") or [])]
    if not frames:
        dur = clip_duration(clip) or 0.0
        n = int(dur // interval) + 1 if dur else 0
        frames = [round(i * interval, 2) for i in range(n)]
    return {"interval_s": interval, "frames": frames,
            "sheets": int(d.get("sheets_read") or 0),
            "prompt_version": int(params.get("prompt_version") or 1)}


def bin_picks(edl: dict | None = None) -> list[dict]:
    """Every pick in the bin, verdicts re-attached — /api/picks and the first pass's
    picks step build them the one way."""
    payload = project_payload()
    edl = edl if edl is not None else selects.ensure(read_edl())
    return picks.build(payload["clips"], payload["events"], themes=edl.get("themes") or [],
                       story=edl.get("story"), telemetry=bin_telemetry(),
                       verdicts=edl["floor"]["verdicts"], selects=edl["selects"])


@app.get("/api/picks")
def api_picks(order: str = "rank") -> JSONResponse:
    """Every pick in the bin with stored verdicts re-attached, best first or by clip."""
    if order not in ("rank", "clip"):
        raise HTTPException(400, f"unknown order {order!r}")
    # The full project payload, not the Ask's slimmer clip shape: picks need the R8
    # candidates, which the Ask never reads.
    edl = selects.ensure(read_edl())
    released = set(released_clips())
    themes = edl.get("themes") or []
    rows = bin_picks(edl)
    junk_of = {r["clip"]: r["state"] for r in junk_rows(edl)}
    for p in rows:
        p["released"] = p["clip"] in released
        p["junk"] = junk_of.get(p["clip"], "clean")
    ordered = picks.order(rows, order)
    # Whether a first pass has made its picks (it finished, or stopped or failed in the
    # cut step, or one is running past them): the board lists the picks in Selects and
    # marks them on the cut from then on (verdict 4, punch 1).
    rec, live = _fp_record() or {}, _fp_live()
    made = ((rec.get("produced") or {}).get("picks") is not None or rec.get("step") == "cut"
            or bool(live and live.get("picks") is not None))
    return JSONResponse({
        "firstpass_picked": made,
        "picks": _pick_rows(ordered),
        "rounds": len(picks.rounds([p for p in ordered if p["released"]])),
        "round_size": picks.ROUND_SIZE,
        "released": sorted(released),
        "summary": selects.summary(edl),
        "position": edl["floor"]["position"],
        "themes": themes,
        "dictation": dictate.available(),
        "looked": {c: looked_at(c) for c in footage_clips()},
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
            source=str(body.get("source") or "floor"),
            # The keep's fingerprint, so relink can find its footage after a rename.
            clip_duration=clip_duration(clip))
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
    # A select whose clip left the folder is flagged, never dropped; one whose clip came
    # back under another name is re-pointed by its recorded length. Persisted only when
    # something actually changed, so a plain read never rewrites the EDL.
    before = json.dumps(edl["selects"], sort_keys=True)
    selects.relink(edl, {c: {"duration": clip_duration(c)} for c in footage_clips()})
    if json.dumps(edl["selects"], sort_keys=True) != before:
        write_edl(edl)
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
    except dictate.TooLong as exc:
        raise HTTPException(413, str(exc))
    except dictate.NotAvailable as exc:
        raise HTTPException(501, str(exc))
    finally:
        path.unlink(missing_ok=True)
    return JSONResponse(result)


@app.get("/floor", response_class=HTMLResponse)
def floor_page() -> HTMLResponse:
    return HTMLResponse((HERE / "static" / "floor.html").read_text(encoding="utf-8"),
                        headers=NO_STORE)


@app.get("/open", response_class=HTMLResponse)
def open_page() -> HTMLResponse:
    """The open screen (INTAKE M5): the folder as a contact sheet, and the index."""
    p = HERE / "static" / "open.html"
    if not p.exists():
        raise HTTPException(404, "the open screen is not built yet — see docs/INTAKE.md M5")
    return HTMLResponse(p.read_text(encoding="utf-8"), headers=NO_STORE)


@app.get("/open.js")
def open_js() -> Response:
    p = HERE / "static" / "open.js"
    if not p.exists():
        raise HTTPException(404, "the open screen's script is not built yet")
    return Response(p.read_text(encoding="utf-8"),
                    media_type="application/javascript", headers=NO_STORE)


@app.get("/floor.js")
def floor_js() -> Response:
    p = HERE / "static" / "floor.js"
    if not p.exists():
        raise HTTPException(404, "the floor's script is not built yet")
    return Response(p.read_text(encoding="utf-8"),
                    media_type="application/javascript", headers=NO_STORE)


# ---------------------------------------------------------------- junk (HANDOFF item 5)
#
# Proposed by a measurement, confirmed by the human: `roughcut/junk.py` reads the
# colour file the proxy stage already wrote, the audio sidecar and the duration, and
# says which clips look like a camera in a pocket. Nothing here decodes video unless a
# clip has a proxy and no colour file yet (one 5 s sample per frame, from the proxy).
# The EDL's `junk` block holds only the human's word; the proposals are recomputed on
# every read, cached by the files' mtimes, so they cost a stat per clip.


def junk_block(edl: dict | None = None) -> dict:
    block = (edl if edl is not None else read_edl()).get("junk")
    return block if isinstance(block, dict) else {}


def confirmed_junk(edl: dict | None = None) -> set[str]:
    return junkmod.confirmed(junk_block(edl))


def junk_assess(clip: str) -> dict:
    """The proposal for one clip, from the files on disk only — it never measures
    (verdict 7 blocker 3: measuring a fresh proxy here held the board's boot ~30 s).
    A clip with no colour file is judged on its length alone, and says so
    (`numbers.samples` is 0); `junk_rows` marks it pending when a measurement is due."""
    cfile = colour_file(clip)
    sfile: Path = STATE["sidecars"] / f"{Path(clip).stem}.audio.json"
    mtime = lambda p: p.stat().st_mtime if p.exists() else None   # noqa: E731
    key = (str(cfile), mtime(cfile), str(sfile), mtime(sfile))
    cache: dict = STATE.setdefault("junk_cache", {})
    hit = cache.get(clip)
    if hit and hit[0] == key:
        return hit[1]
    side = load_sidecar(clip) or None
    out = junkmod.assess(junkmod.facts(load_colour(clip), side, clip_duration(clip)))
    cache[clip] = (key, out)
    return out


def junk_rows(edl: dict | None = None) -> list[dict]:
    """Every footage clip with the machine's proposal, the human's verdict and the one
    word that follows from both. Generated clips are never assessed — a black slide
    the editor asked for is the point of it. `colour` says what the proposal stands
    on: "measured" (a colour file), "pending" (a proxy waiting for its measurement:
    the proposal may change when it lands) or "none" (no proxy yet)."""
    edl = edl if edl is not None else read_edl()
    block = junk_block(edl)
    used = {s.get("clip") for s in edl.get("segments") or []}
    rows = []
    for clip in footage_clips():
        a = junk_assess(clip)
        verdict = (block.get(clip) or {}).get("verdict")
        stem = Path(clip).stem
        cstate = ("measured" if colour_file(clip).exists()
                  else "pending" if colour_pending(clip) else "none")
        rows.append({"clip": clip, "stem": stem,
                     "state": junkmod.status(a["junk"], verdict),
                     "proposed": a["junk"], "verdict": verdict,
                     "colour": cstate,
                     "reasons": a["reasons"], "numbers": a["numbers"],
                     "duration": clip_duration(clip), "used": clip in used,
                     "proxy": f"/media/proxy/{stem}.mp4",
                     "poster": f"/media/poster/{stem}.jpg"})
    return rows


def junk_counts(rows: list[dict]) -> dict:
    return {s: sum(1 for r in rows if r["state"] == s) for s in junkmod.STATES}


def journal_junk(row: dict) -> str | None:
    """What the index journal should hold for a clip's junk row."""
    return {"confirmed": "confirmed", "proposed": "proposed"}.get(row["state"])


def sync_junk(j: journal.Journal) -> None:
    """Bend the journal to the bin's junk word: confirmed clips skip their priced
    stages, proposed ones wait behind the clean ones, a kept or cleared one gets its
    stages back. Called by the index thread between stages — it owns the journal while
    it runs — and by POST /api/junk when no index is running."""
    for row in junk_rows():
        clip = row["clip"]
        if clip in j.clips and j.clips[clip].get("junk") != journal_junk(row):
            j.set_junk(clip, journal_junk(row))


@app.get("/api/junk")
def api_junk() -> JSONResponse:
    """The junk proposals with their reasons and numbers, and the editor's verdicts.
    Free: it reads the files the index already wrote and never measures (verdict 7
    blocker 3). A clip whose proxy landed before its colour file is `colour: "pending"`;
    those go to the background colour queue, and `pending` counts them so the board can
    say "checking colour… N clips" and read again."""
    rows = junk_rows()
    waiting = [r["clip"] for r in rows if r.get("colour") == "pending"]
    if waiting:
        queue_colour(waiting)
    return JSONResponse({"clips": rows, "counts": junk_counts(rows),
                         "pending": len(waiting),
                         "thresholds": {"luma_black": junkmod.LUMA_BLACK,
                                        "luma_lit": junkmod.LUMA_LIT,
                                        "flat_spread": junkmod.FLAT_SPREAD,
                                        "short_s": junkmod.SHORT_S},
                         "provisional": True})


@app.post("/api/junk")
async def api_junk_post(request: Request) -> JSONResponse:
    """`{clip, verdict: "junk" | "keep" | null}` — the editor's answer to a proposal
    (or a clip marked by hand). `junk` takes the clip out of the Ask, Find and the
    bin's default view and skips its priced index stages; `keep` overrides the
    proposal; null forgets the answer and the proposal stands again."""
    body = await request.json()
    clip = str(body.get("clip") or "")
    verdict = body.get("verdict")
    if verdict not in (*junkmod.VERDICTS, None):
        raise HTTPException(400, f"verdict must be one of {junkmod.VERDICTS} or null")
    edl = read_edl()
    if clip not in footage_clips() and clip not in junk_block(edl):
        raise HTTPException(400, f"unknown clip {clip!r}")
    a = junk_assess(clip) if clip in footage_clips() else {"reasons": []}
    edl["junk"] = junkmod.set_verdict(junk_block(edl), clip, verdict,
                                      reasons=a["reasons"])
    if not edl["junk"]:
        edl.pop("junk")
    write_edl(edl)
    rows = junk_rows(edl)
    row = next((r for r in rows if r["clip"] == clip), None)
    # The journal follows now when nothing else holds it; a running index owns its
    # journal in memory and picks the word up before its next stage.
    index = "none"
    skipped: list[str] = []
    if journal_path().exists() and row is not None:
        if any(x["state"] not in progress.TERMINAL for x in INDEXES.values()):
            index = "next stage"
        else:
            try:
                j = load_journal()
                if clip in j.clips:
                    skipped = j.set_junk(clip, journal_junk(row))
                index = "applied"
            except journal.JournalError:
                index = "unreadable"
    return JSONResponse({"ok": True, "clip": row, "counts": junk_counts(rows),
                         "index": index, "stages": skipped})


# ---------------------------------------------------------------- backend


BACKEND: dict = {"state": "unknown", "detail": "", "latency_ms": None, "fix": None}
# Set on the thread running the backend probe: the project's cap does not gate it.
_GATE_OFF = threading.local()


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
    cli: dict = {}
    if name == "claude_cli":
        if shutil.which("claude") is None:
            problems.append(
                "claude CLI not on PATH — it installs to ~/.local/bin, which a "
                "non-login shell does not pick up. Start from `bash -l`, or set "
                "ROUGHCUT_BACKEND=anthropic_api.")
        elif STATE.get("cli_facts"):
            # Only a real launch asks the CLI about itself; the suite's in-process
            # servers must not depend on whoever is signed in on the box running it.
            cli = cli_facts()
    elif name == "anthropic_api":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            problems.append("ANTHROPIC_API_KEY is not set.")
    else:
        problems.append(f"unknown ROUGHCUT_BACKEND {name!r}")
    return {
        "backend": name,
        "model": config.model_for(config.ROLE_SKELETON),
        "models": {"deep": config.model_for(config.ROLE_SKELETON),
                   "quick": config.model_for(config.ROLE_ANALYSIS)},
        "problems": problems,
        "cli": cli,
        "budget_usd": budget_cap(),
        "spent_usd": project_spent(),               # this project's, from disk
        "charged": config.charges_money(),          # False: the $ are API-equivalent
        **BACKEND,
        # after BACKEND, whose own `fix` is only the probe's half of the answer
        "fix": backend_fix(problems, cli),
    }


# The free half of "does the CLI need Karl": its version and whether it is signed in,
# both answered by the CLI itself without a model call. Cached because /api/status
# carries this and is polled; a sign-in done at the terminal shows within the TTL, or
# at once on *Check again*, which drops the cache.
CLI_FACTS_TTL_S = 30.0
_CLI_FACTS: dict = {"at": 0.0, "facts": {}}


def cli_facts(fresh: bool = False) -> dict:
    if not fresh and time.time() - _CLI_FACTS["at"] < CLI_FACTS_TTL_S:
        return _CLI_FACTS["facts"]
    facts: dict = {}
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True, text=True,
                             timeout=15).stdout.strip()
        facts["version"] = out.split()[0] if out else None
    except (OSError, subprocess.TimeoutExpired):
        facts["version"] = None
    try:
        # With the year-long token (`inference.cli_env`) this reports signed in without
        # checking the token is still good — an expired one is the probe's to catch.
        proc = subprocess.run(["claude", "auth", "status"], capture_output=True,
                              text=True, timeout=15, env=inference.cli_env())
        auth = json.loads(proc.stdout or "{}")
        facts["logged_in"] = bool(auth.get("loggedIn"))
        facts["auth"] = auth.get("authMethod")
        facts["plan"] = auth.get("subscriptionType")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        facts["logged_in"] = None          # unknown is not "signed out"
    _CLI_FACTS.update(at=time.time(), facts=facts)
    return facts


def backend_fix(problems: list[str], cli: dict) -> dict | None:
    """The one thing Karl must do at a terminal, if there is one — the banner's
    content. Free evidence first (a signed-out CLI is known without a call), then the
    probe's verdict, then whatever a real job most recently ran into."""
    for p in problems:
        found = inference.diagnose(p)
        if found:
            return found
    if cli.get("logged_in") is False:
        return inference.diagnose("not logged in")
    return BACKEND.get("fix") or inference.last_problem()


def _probe_job() -> None:
    """One deliberately tiny live call, to answer the question preflight cannot: is
    this backend actually authenticated?

    It runs on the cheap per-unit role rather than the skeleton role — it is proving
    the door opens, not that the big model is available — and it is one call, which
    matters more than its token count on a subscription where the scarce resource is
    requests per rolling window.
    """
    t0 = time.time()
    # One tiny call per *distinct model*, not per role: the deep model is the one an
    # out-of-date CLI rejects (Opus 5.5 needs 2.1.280+), and a probe on the quick model
    # alone said "ready" over a CLI that would fail every Ask.
    roles = {config.model_for(r): r for r in (config.ROLE_ANALYSIS, config.ROLE_SKELETON)}
    result = None
    # The project's cap is not asked (M16 review): the probe proves the door opens, for
    # a fraction of a cent, and is no part of the project's work. Asked, it was refused
    # within $0.05 of a cap — where the index's own pause leaves a project — and the
    # pill called a working CLI failed, in the cap's words.
    _GATE_OFF.probe = True
    try:
        for model, role in roles.items():
            try:
                result = inference.complete("Reply with exactly: OK", role=role)
            except inference.InferenceError as exc:
                BACKEND.update(state="failed", detail=f"{model}: {str(exc)[:380]}",
                               fix=inference.diagnose(str(exc)),
                               latency_ms=int((time.time() - t0) * 1000))
                return
    finally:
        _GATE_OFF.probe = False
    BACKEND.update(state="ok", detail=str(result.content).strip()[:80] if result else "",
                   fix=None, latency_ms=int((time.time() - t0) * 1000))


def probe_backend() -> None:
    if BACKEND["state"] == "checking":
        return
    # *Check again* means everything: the cached CLI facts, the last job's diagnosis.
    cli_facts(fresh=True)
    inference.clear_problem()
    BACKEND.update(state="checking", detail="", latency_ms=None, fix=None)
    threading.Thread(target=_probe_job, daemon=True).start()


@app.post("/api/backend/probe")
def api_backend_probe() -> JSONResponse:
    probe_backend()
    return JSONResponse(backend_preflight())


@app.get("/api/backend")
def api_backend() -> JSONResponse:
    """The backend alone — what the banner on every screen polls, without the rest of
    /api/status (the pass and the open screen have no use for the board's status)."""
    return JSONResponse(backend_preflight())


# ---------------------------------------------------------------- the flow (INTAKE M14)
#
# Karl, 2026-10-03 (#3): "make the flow through various stages make more sense in the
# UI." One model of where the project is, computed here from files and drawn by
# /flow.js on all three screens — `roughcut/flow.py` is the logic, this is the facts.


def priced_waiting(look: list[str], close: list[str]) -> dict:
    """What looking at `look` and closely at `close` would cost — the index job's own
    arithmetic (a sheet per `sheets_for`, three windows per close look), so the price
    the bar and the paused box say is the price the run then spends."""
    usd = (sum(sheets_for(c) for c in look) * VISUAL_USD_PER_SHEET
           + len(close) * FINE_WINDOWS_PER_CLIP * FINE_USD_PER_WINDOW)
    return {"clips": len(set(look) | set(close)), "looks": len(look),
            "close": len(close), "usd": round(usd, 2)}


def journal_waiting(j: journal.Journal) -> dict:
    """The priced work a journal still holds, by the journal's own word on each clip
    (a junk-confirmed clip's stages are skipped, so it is never in here)."""
    present = set(footage_clips())
    live = [c for c in j.clips if c in present and not j.clips[c].get("missing")]
    look = [c for c in live if j.state(c, "look") in journal.RETRYABLE]
    close = [c for c in live if j.state(c, "close") in journal.RETRYABLE]
    return priced_waiting(look, close)


_INTERVAL_CACHE: dict = {}


def look_intervals(stems: set[str]) -> dict[str, int]:
    """How many clips were looked at at each interval, from the coarse sidecars' own
    `params.interval_s` — the granularity, in data the index already wrote. Cached by
    mtime: the bar polls this."""
    out: dict[str, int] = {}
    for stem in stems:
        p: Path = STATE["visual"] / f"{stem}.visual.json"
        try:
            key = (str(p), p.stat().st_mtime)
        except OSError:
            continue
        if _INTERVAL_CACHE.get(str(p), (None,))[0] != key:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                iv = float((d.get("params") or {}).get("interval_s") or VISUAL_INTERVAL_S)
            except (OSError, ValueError, TypeError):
                iv = VISUAL_INTERVAL_S
            _INTERVAL_CACHE[str(p)] = (key, iv)
        iv = _INTERVAL_CACHE[str(p)][1]
        out[f"{iv:g}"] = out.get(f"{iv:g}", 0) + 1
    return out


def ask_pending(edl_mtime: float) -> dict | None:
    """The newest Ask proposal, when nobody has answered it: not marked answered (the
    board's Discard marks it, `POST /api/asks/answer`) and newer than the cut on disk
    (an Accept saves the cut, which answers it)."""
    files = sorted(STATE["asks"].glob("*.json"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    if not files:
        return None
    try:
        rec = json.loads(files[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if rec.get("answered") or float(rec.get("created") or 0) <= edl_mtime:
        return None
    plan = rec.get("plan") or {}
    return {"job": rec.get("job"), "shots": len(plan.get("segments") or []),
            "colour_only": bool(plan.get("unchanged"))}


def _same_shots(a: list[dict] | None, b: list[dict]) -> bool:
    """A render's shot list against the cut — the board's own `isThisCut` rule, and
    the same Effect Controls (they render now: a film made before a shot was scaled
    is not that cut)."""
    if not a or len(a) != len(b):
        return False
    return all(x.get("clip") == y.get("clip")
               and abs(float(x.get("in", 0)) - float(y.get("in", 0))) < 0.005
               and abs(float(x.get("out", 0)) - float(y.get("out", 0))) < 0.005
               and float(x.get("speed") or 1) == float(y.get("speed") or 1)
               and (x.get("controls") or {}) == (y.get("controls") or {})
               for x, y in zip(a, b))


def render_facts(segments: list[dict]) -> dict:
    metas = []
    for meta_path in STATE["renders"].glob("cut_*.json"):
        try:
            metas.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    metas = [m for m in metas if (STATE["renders"] / f"cut_{m.get('job')}.mp4").exists()]
    metas.sort(key=lambda m: float(m.get("created") or 0), reverse=True)
    return {"count": len(metas),
            "latest_matches": bool(metas) and _same_shots(metas[0].get("shots"), segments),
            "matches": any(_same_shots(m.get("shots"), segments) for m in metas)}


def running_jobs() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for kind, reg in (("index", INDEXES), ("analyse", ANALYSES), ("visual", VISUALS),
                      ("themes", THEMES), ("ask", ASKS), ("fx", FX), ("render", RENDERS)):
        live = [x for x in reg.values() if x["state"] not in progress.TERMINAL]
        if live:
            snap = live[-1].snapshot()
            out[kind] = {"id": snap.get("id"), "pct": snap.get("pct"),
                         "detail": snap.get("detail") or ""}
    return out


def fx_target(proposed: list[dict], segments: list[dict]) -> dict | None:
    """Where Next lands for the waiting effects (INTAKE M16 I16.0a): the proposal on
    the earliest shot of the cut (then the oldest), as `{shot, fx}` — the shot the
    board selects and the card the FX tool brings into view. Next used to open the
    tool on whatever shot the board had anchored: shot 1's accepted title, while the
    slow motion waited on shot 17. A proposal for a shot the edits will create sits
    on its `anchor_shot`. `flow_facts` passes only proposals on a shot of the cut; one
    given here whose shot is not in it carries only `fx`."""
    if not proposed:
        return None
    order = {str(s.get("id")): i for i, s in enumerate(segments) if s.get("id")}

    def shot_of(e: dict) -> str | None:
        sid = str(e.get("anchor_shot") or e.get("shot") or "")
        return sid if sid in order else None

    first = min(proposed, key=lambda e: (order.get(shot_of(e) or "", len(order)),
                                         str(e.get("created") or ""), str(e.get("id"))))
    out = {"fx": str(first.get("id"))}
    if shot_of(first):
        out["shot"] = shot_of(first)
    return out


def _flow_disk_facts() -> dict:
    """Everything `flow.compute` reads, off the files: footage, proxies, the journal,
    the sidecars, the EDL's brief / selects / cut / polish blocks, the asks and the
    renders on disk, and the CLI's fix (`flow_facts` adds the running jobs). Nothing
    here spends."""
    clips = footage_clips()
    edl = read_edl()
    edl_mtime = STATE["edl"].stat().st_mtime
    junk = confirmed_junk(edl) & set(clips)
    clean = [c for c in clips if c not in junk]
    heard = analysed_stems()
    looked_stems, close_stems = visual_stems(), fine_stems()
    on_pass = released_clips()
    ix: dict = {"journal": journal_path().exists(), "interval_s": look_interval(),
                "listened": sum(1 for c in clips if Path(c).stem in heard),
                "on_pass": len(on_pass), "paused": False, "released": 0, "parked": 0}
    j = None
    if ix["journal"]:
        try:
            j = load_journal()
        except journal.JournalError:
            j = None
    if j is not None:
        live = [c for c in clean if c in j.clips and not j.clips[c].get("missing")]
        settled = lambda c, s: j.state(c, s) in journal.SETTLED   # noqa: E731
        ix["looked"] = sum(1 for c in clean if (c in j.clips and settled(c, "look"))
                           or Path(c).stem in looked_stems)
        ix["close"] = sum(1 for c in clean if (c in j.clips and settled(c, "close"))
                          or Path(c).stem in close_stems)
        prog = j.progress()
        ix.update(paused=j.paused_priced, paused_reason=j.data.get("paused_reason"),
                  released=prog.get("released", 0), parked=prog.get("parked", 0),
                  waiting=journal_waiting(j))
        pend_look = [c for c in live if j.state(c, "look") in journal.RETRYABLE]
        pend_close = [c for c in live if j.state(c, "close") in journal.RETRYABLE]
    else:
        ix["looked"] = sum(1 for c in clean if Path(c).stem in looked_stems)
        ix["close"] = sum(1 for c in clean if Path(c).stem in close_stems)
        # Without a journal a clip with nothing worth a close look leaves no file, so
        # the close look is not a requirement here — only the coarse look is.
        ix["close_need"] = ix["close"]
        pend_look = [c for c in clean if Path(c).stem not in looked_stems]
        pend_close = [c for c in clean if Path(c).stem not in close_stems]
    ix["pending_usd"] = priced_waiting(pend_look, pend_close)["usd"]
    ix["intervals"] = look_intervals({Path(c).stem for c in clean} & looked_stems)

    themes_done = any(t["state"] == "done" and t.get("proposal") for t in THEMES.values())
    brief = {"story": bool(str(edl.get("story") or "").strip()),
             "themes": len([t for t in (edl.get("themes") or []) if isinstance(t, str)]),
             "proposal": themes_done or proposal_path().exists()}

    sel = selects.ensure(json.loads(json.dumps(edl)))
    selects.used_in(sel)
    passed = ({v.get("clip") for v in sel["floor"]["verdicts"]}
              | {s.get("clip") for s in sel["selects"]}) & set(on_pass)
    heroes = [s for s in sel["selects"] if s.get("hero")]
    pas = {"on_pass": len(on_pass), "passed": len(passed), "keeps": len(sel["selects"]),
           "heroes": len(heroes), "heroes_out": sum(1 for s in heroes if not s.get("used_in"))}

    segments = [s for s in edl.get("segments") or [] if isinstance(s, dict)]
    cut = {"shots": len(segments), "length_s": round(sum(edits.dur(s) for s in segments), 2),
           "target": edl.get("target_s"), "proposal": ask_pending(edl_mtime)}
    colour = edl.get("colour") or {}
    look = ("off" if colour.get("mode") == "off" else colour.get("look")) or None
    # A proposal waits on this cut only while its shot (or the shot it is anchored to) is
    # in it: the FX tool lists cards per shot of the cut, so one whose shot left — or
    # one from a sibling cut of the bin, whose ids differ — has no card to answer it
    # on, and would hold Next and the badge for good. It stays on disk and comes back
    # with its shot (an undo).
    in_cut = {str(s.get("id")) for s in segments if s.get("id")}
    proposed = [e for e in _fx_all() if e.get("status") == "proposed"
                and (str(e.get("shot")) in in_cut or str(e.get("anchor_shot") or "") in in_cut)]
    polish = {"effects": len(edl.get("effects") or []), "fx_proposed": len(proposed),
              "target": fx_target(proposed, segments),
              "look": look, "music": bool((edl.get("effects_music") or {}).get("asset"))}
    proxies = sum(1 for c in clips if (STATE["proxy_dir"] / f"{Path(c).stem}.mp4").exists())
    return {"clips": len(clips), "junk": len(junk),
            "proxies": {"done": proxies, "total": len(clips)},
            "index": ix, "brief": brief, "pass": pas, "cut": cut, "polish": polish,
            "render": render_facts(segments), "fix": backend_preflight().get("fix")}


# ---------------------------------------------------------------- status snapshots (wave 11 CHEAP)
#
# Verdict 8, punch 10: during an export and the proxy builds `/api/flow`, `/api/status`
# and `/api/firstpass` took 0.5-1.5 s. Measured (lane CHEAP, Killington's work dir):
# 14 / 12 / 17 ms idle, but each answer is a few hundred small file reads and every one
# drops the GIL; with one busy Python thread beside it (an export's range reads, the
# proxy walk, the review copy) each read waits a switch interval to get it back, and
# the same answers took 128 / 121 / 144 ms (p95 213-318) — with the judge's several,
# the 0.5-1.5 s. So the three are served from a snapshot of what they read on disk:
#
#   * `_snapped(name, build)` keeps `build()`'s result under a key that changes when
#     anything it reads changes: `SNAP["gen"]`, bumped by the directory watch
#     (`roughcut.dirwatch`: inotify on the folders and files these read, drained with
#     one non-blocking read; the kernel queued a write the moment it happened, so a
#     file written just before a poll is that poll's news), plus the in-memory things
#     they read (the job registries' states, the first pass, the backend probe, the
#     ROUGHCUT_ settings, and the helpers themselves — a monkeypatched one is new code);
#   * what changes every second — the running jobs' progress, the proxy walk's
#     counters, a running pass's Ask detail — is merged in from memory per call;
#   * the endpoints are `async`: a snapshot hit answers on the event loop without a
#     thread hop (the last bytes, when nothing live moved); a miss rebuilds on a worker
#     thread (`snap_respond`).
# Without inotify every call is a miss: as correct, and as slow, as before.

SNAP: dict = {"gen": 0, "armed": None, "rearm": True, "dirty": [], "cache": {},
              "bytes": {}, "lock": threading.Lock()}
SNAP_WATCH = dirwatch.DirWatch()
# a helper the snapshot reads, replaced (the suite's monkeypatch), is a change
_SNAP_FNS = ("footage_clips", "analysed_stems", "visual_stems", "fine_stems",
             "released_clips", "load_journal", "journal_waiting", "junk_block",
             "confirmed_junk", "junk_rows", "clip_duration", "look_interval",
             "look_intervals", "priced_waiting", "ask_pending", "ask_plans", "ask_price",
             "_ask_records", "_fp_record", "_fx_all", "fx_target", "render_facts",
             "backend_preflight", "backend_fix", "cli_facts", "project_spent",
             "budget_cap", "visual_status", "audit_plan", "audit_price", "firstpass_plan",
             "read_edl", "sheets_for", "running_jobs")


def _snap_paths() -> tuple:
    return tuple(str(STATE.get(k)) for k in ("edl", "footage", "work", "sidecars", "visual",
                                              "asks", "proxy_dir", "renders"))


def _snap_targets() -> list[tuple[Path, tuple[str, ...]]]:
    """The folders the snapshot reads and the names in them that matter."""
    files = [STATE["edl"], journal_path(), proposal_path(), firstpass_path(), spend_path(),
             settings_path()]
    media = mediamod.media_dir(STATE["work"], STATE["footage"].name)
    return ([(STATE["footage"], ("*",)), (STATE["sidecars"], ("*.audio.json",)),
             (STATE["visual"], ("*",)), (STATE["asks"], ("*.json",)),
             (fx_home(), ("fx_*",)), (STATE["proxy_dir"], ("*.mp4",)),
             (STATE["renders"], ("cut_*",)), (generated_dir(), ("*.mp4",)),
             # imports and uploads (wave 12 IMPORT): the list, and the uploaded files
             (media, (mediamod.SOURCES_FILE,)), (media / "uploads", ("*",))]
            + [(f.parent, (f.name,)) for f in files])


def snap_poll(blocking: bool = True) -> None:
    """Drain the watch (one non-blocking read; safe on the event loop with `blocking`
    False, which never waits for a re-arm). A change bumps the generation and is kept
    for `_snap_settle`."""
    ch = SNAP_WATCH.changes(blocking)
    if ch is None or ch:
        SNAP["gen"] += 1
        SNAP["rearm"] = True
        if ch:
            SNAP["dirty"].extend(ch)


def _snap_settle() -> None:
    """Off the event loop: re-arm the watch after a change (a folder may have appeared)
    or a re-point, and act on what changed (a clip's sound heard → its peaks)."""
    with SNAP["lock"]:
        paths = _snap_paths()
        if not SNAP["rearm"] and SNAP["armed"] == paths:
            return
        dirty, SNAP["dirty"] = SNAP["dirty"], []
        SNAP["rearm"] = False               # a change from here on asks for another
        try:
            SNAP_WATCH.arm(_snap_targets())
        except (KeyError, OSError):         # not configured yet
            pass
        SNAP["armed"] = paths
        SNAP["gen"] += 1                    # whatever happened while arming is read now
    peaks_changed(dirty)


def _snap_key() -> tuple:
    g = globals()
    return (SNAP["gen"], SNAP["armed"] == _snap_paths(),
            tuple((id(j), j.get("state"), bool(j.get("proposal"))) for j in all_jobs()),
            tuple((k, x.get("state"), x.get("stage"), x.get("picks"), x.get("ask"),
                   repr(x.get("steps"))) for k, x in FIRSTPASS.items()),
            tuple(id(g.get(n)) for n in _SNAP_FNS),
            (id(shutil.which), id(inference.last_problem)),
            repr(sorted(BACKEND.items())), repr(inference.last_problem()),
            tuple(sorted((k, v) for k, v in os.environ.items()
                         if k.startswith("ROUGHCUT_"))),
            bool(os.environ.get("ANTHROPIC_API_KEY")),
            # the CLI's sign-in is re-asked once its cache is stale (`cli_facts`)
            int(time.time() // CLI_FACTS_TTL_S) if STATE.get("cli_facts") else 0,
            STATE.get("edl_created"), _snap_paths())


def snap_hit(name: str) -> bool:
    """Is `name`'s snapshot current? Event-loop safe: no disk, one read of the watch."""
    snap_poll(blocking=False)
    if SNAP["rearm"] or SNAP["armed"] != _snap_paths():
        return False
    hit = SNAP["cache"].get(name)
    return hit is not None and hit[0] == _snap_key()


def _snapped(name: str, build: Callable[[], dict]) -> dict:
    """`build()`'s result, rebuilt only when something it reads has changed. The key is
    taken before the build, so a change during it makes the next call rebuild. The
    result is shared: callers copy before they change it."""
    if not getattr(_SNAP_LOCAL, "fresh", False):     # `snap_respond` just looked
        snap_poll()
        _snap_settle()
    key = _snap_key()
    hit = SNAP["cache"].get(name)
    if hit is not None and hit[0] == key:
        return hit[1]
    val = build()
    SNAP["cache"][name] = (key, val)
    return val


_SNAP_LOCAL = threading.local()


def _json_bytes(content) -> bytes:
    """What JSONResponse would send."""
    return json.dumps(content, ensure_ascii=False, allow_nan=False, indent=None,
                      separators=(",", ":")).encode("utf-8")


async def snap_respond(name: str, fn: Callable[[], dict],
                       live: Callable[[], object] = lambda: None) -> Response:
    """`fn()`'s answer. When `name`'s snapshot is current it is made on the event loop
    without a thread hop or a disk read — and when `live()` (the in-memory part fn
    merges in) is unchanged too, it is the last answer's bytes; else on a worker
    thread, where the rebuild may read the disk."""
    if snap_hit(name):
        _SNAP_LOCAL.fresh = True
        try:
            lk = (SNAP["cache"][name][0], live())
            hit = SNAP["bytes"].get(name)
            if hit is not None and hit[0] == lk:
                return Response(hit[1], media_type="application/json")
            body = _json_bytes(fn())
            SNAP["bytes"][name] = (lk, body)
            return Response(body, media_type="application/json")
        finally:
            _SNAP_LOCAL.fresh = False
    return JSONResponse(await asyncio.to_thread(fn))


def flow_facts() -> dict:
    """`_flow_disk_facts` from the snapshot (rebuilt only when something it reads
    changed: `_snapped`), with the running jobs merged in from memory on every call —
    their progress changes every second and costs nothing to read."""
    facts = dict(_snapped("flow", _flow_disk_facts))
    facts["jobs"] = running_jobs()
    return facts


@app.get("/api/flow")
async def api_flow() -> JSONResponse:
    """Where the project is, as seven stages and one next action — what /flow.js draws
    on every screen. The facts are the files' (a snapshot rebuilt when one changes, off
    the event loop) and the running jobs' (memory); see `_snapped`."""
    return await snap_respond("flow", lambda: flowmod.compute(flow_facts()),
                              live=lambda: repr(running_jobs()))


@app.post("/api/asks/answer")
async def api_asks_answer(request: Request) -> JSONResponse:
    """The board's Discard (or Accept) of the newest Ask proposal, written into its
    record — so the flow stops calling a discarded proposal "waiting for you". Accept
    also saves the cut, which answers it on its own; this makes Discard say so too."""
    body = await request.json() if int(request.headers.get("content-length") or 0) else {}
    return JSONResponse(answer_ask(body if isinstance(body, dict) else {}))


def answer_ask(body: dict) -> dict:
    """`POST /api/asks/answer`'s work, also done by a save that carries `answer` (the
    board's flush on unload, wave 8 SAVESAFE). Raises HTTPException(400) on a bad one."""
    answer = str(body.get("answer") or "")
    if answer not in ("accept", "discard"):
        raise HTTPException(400, "answer must be accept or discard")
    files = sorted(STATE["asks"].glob("*.json"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    if not files:
        return {"ok": True, "job": None}
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    rec["answered"] = {"answer": answer, "at": time.time()}
    partial = body.get("partial")
    if answer == "accept" and isinstance(partial, dict):
        rec["answered"]["partial"] = partial      # which shots the Accept took (PICKS)
    tmp = files[0].with_name(files[0].name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    tmp.replace(files[0])
    if rec["answered"].get("partial"):
        note_partial_accept(rec)                  # provenance: the save may have landed
        note_rejections(rec)                      # verdict 4: a rejection is remembered
    if answer == "accept":
        MEDIA["kick"].set()     # verdict 8: an idle media walk looks at the accepted cut now
    return {"ok": True, "job": rec.get("job")}


def note_rejections(rec: dict, edl: dict | None = None) -> None:
    """The ranges a partial Accept turned down (`firstpass.rejected_ranges`), on the
    cut: a re-run is told not to propose them and is held to it. Called by the answer
    (it writes the EDL) and by the save that answers it (`edl`, written by that save) —
    whichever lands first; a range is kept once. Never fails either."""
    try:
        partial = (rec.get("answered") or {}).get("partial")
        ranges = fpmod.rejected_ranges(rec.get("plan") or {}, partial)
        if not ranges:
            return
        target = edl if edl is not None else read_edl()
        at = (rec.get("answered") or {}).get("at")
        if fpmod.record_rejections(target, ranges, job=rec.get("job"), at=at) and edl is None:
            write_edl(target)
    except Exception as exc:  # noqa: BLE001 — a rejection note must never cost a save
        print(f"  !! provenance (rejections): {exc}", flush=True)


@app.get("/flow.js")
def flowjs() -> Response:
    """The flow bar every screen carries (INTAKE M14)."""
    return Response((HERE / "static" / "flow.js").read_text(encoding="utf-8"),
                    media_type="application/javascript", headers=NO_STORE)


# ---------------------------------------------------------------- the first pass (K1)
#
# Karl, 2026-10-14: "it is not clear how to re run the first pass." One home for it:
# `GET /api/firstpass` says where it stands (never run / running {stage, counts} / done
# {when, what it produced} / stale after edits), what a run would do and what it costs
# on this backend; `POST /api/firstpass` runs index → picks → a proposed cut as one job,
# reusing the index job, the picks builder and the Ask's own call. A re-run keeps the
# editor's hand edits exactly (`roughcut/firstpass.py` holds the rules); its result is
# an ordinary Ask proposal, so the board's Accept / Discard / Play-it read it as is.

FIRSTPASS: dict[str, dict] = {}
FIRSTPASS_LOCK = threading.Lock()
FIRSTPASS_POLL_S = 0.5


def firstpass_path() -> Path:
    return fpmod.record_path(STATE["work"], STATE["footage"].name)


def _fp_record() -> dict | None:
    try:
        rec = json.loads(firstpass_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def _fp_save(rec: dict) -> None:
    p = firstpass_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    tmp.replace(p)


def _ask_records(newest: int | None = None) -> list[dict]:
    d = STATE.get("asks")
    files = sorted(d.glob("*.json"), key=lambda q: q.stat().st_mtime,
                   reverse=True) if d and d.is_dir() else []
    out = []
    for p in files[:newest] if newest else files:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def ask_plans() -> list[list[dict]]:
    """The shots of every proposal on disk — what `firstpass.manual_edits` counts as the
    machine's on a cut that has no base yet."""
    return [r["plan"]["segments"] for r in _ask_records()
            if isinstance((r.get("plan") or {}).get("segments"), list)]


def note_accept(edl: dict, saved: list[dict], colour=None) -> None:
    """Provenance, on every save (first pass, K1): a save that is the Accept of a waiting
    proposal makes its shots the machine's — the new base — except the shots it carried
    over unchanged that were the editor's own (a re-run's locked shots, or a hand-trimmed
    shot an Ask left alone), which stay the editor's. Any other save needs nothing
    recorded: `firstpass.manual_edits` diffs the cut against the base. `colour` is the
    colour block this same save writes (an Accept saves the cut and the proposal's grade
    together), so the base's grades are the accepted ones. Never fails a save."""
    try:
        for rec in _ask_records(newest=3):
            plan = rec.get("plan") or {}
            # The board's Accept saves the cut and answers the record in two requests
            # that race: an "accept" answer that landed first is still this save's.
            answer = (rec.get("answered") or {}).get("answer")
            if (answer and answer != "accept") or plan.get("unchanged"):
                continue
            if answer == "accept" and any(b.get("job") == rec.get("job")
                                          for b in fpmod.bases(edl)[:1]):
                return                        # recorded already
            partial = (rec.get("answered") or {}).get("partial") if answer == "accept" else None
            if not partial and not fpmod.is_accept(saved, plan.get("segments") or []):
                continue
            grade = edl.get("colour")
            if colour is not None:
                try:
                    grade = colourmod.validate_colour(colour, looks_library())
                except (ValueError, TypeError):
                    pass
            if partial:
                # the answer landed first: this save is the partial Accept
                prior = {r["id"]: r["how"] for r in fpmod.manual_edits(edl, ask_plans())
                         if r.get("id")}
                hand = fpmod.partial_hand(saved, plan, prior)
                note_rejections(rec, edl)         # this save writes them
            else:
                before = {s.get("id"): s for s in fpmod._segments(edl) if s.get("id")}
                hand = {}
                for row in fpmod.manual_edits(edl, ask_plans()):
                    sid = row.get("id")
                    now = next((s for s in saved if s.get("id") == sid), None)
                    if sid in before and now is not None and fpmod._same(now, before[sid]):
                        hand[sid] = row["how"]
            fpmod.record_base(edl, saved, job=rec.get("job"), hand=hand, colour=grade or {})
            return
    except Exception as exc:  # noqa: BLE001 — provenance must never cost a save
        print(f"  !! provenance: {exc}", flush=True)


def note_partial_accept(rec: dict) -> None:
    """Provenance for a partial Accept whose answer (`partial`: which shots the editor
    took) lands after its save: the cut on disk is the accepted one, so the base is
    recorded here — the shots taken from the proposal are the machine's, the others
    keep the provenance they had. A save that has not landed yet (no accepted change
    on disk) leaves it to `note_accept`. Never fails the answer."""
    try:
        plan = rec.get("plan") or {}
        edl = read_edl()
        ensure_segment_ids(edl)
        if any(b.get("job") == rec.get("job") for b in fpmod.bases(edl)[:1]):
            return
        saved = fpmod._segments(edl)
        changes = fpmod.plan_changes(plan)
        took = sum(1 for s in saved if any(fpmod._same(s, c, speed=False) for c in changes))
        if changes and not took and int((rec["answered"]["partial"] or {}).get("accepted") or 0):
            return                                    # the save is still on its way
        prior = {r["id"]: r["how"] for r in fpmod.manual_edits(edl, ask_plans()) if r.get("id")}
        fpmod.record_base(edl, saved, job=rec.get("job"),
                          hand=fpmod.partial_hand(saved, plan, prior))
        write_edl(edl)
    except Exception as exc:  # noqa: BLE001 — provenance must never cost an answer
        print(f"  !! provenance (partial accept): {exc}", flush=True)


def _fp_live() -> dict | None:
    return next((x for x in FIRSTPASS.values() if x["state"] == "running"), None)


def _fp_index_counts(stage: dict) -> dict:
    c = stage.get("counts") or {}
    out = {k: int(c.get(k) or 0) for k in ("clips", "listened", "looked", "look_need",
                                            "close", "close_need", "proxied")}
    # computed from the counts beside it, so never ahead of them (verdict 1's 87%)
    out["pct"] = int(flowmod.honest_pct(out) * 100)
    out["words"] = flowmod.index_counts_words(out)
    return out


def firstpass_plan(mode: str | None = None, keep=None, *, facts: dict | None = None,
                   edl: dict | None = None, manual: list[dict] | None = None) -> dict:
    """What a run would do, and its cost on this backend: the steps with their own
    prices, the shots a re-run keeps (locked) and the ones it may replace. Reads files;
    never calls a model. Raises ValueError on a bad `mode` or `keep`."""
    edl = edl if edl is not None else read_edl()
    ensure_segment_ids(edl)
    segments = fpmod._segments(edl)
    manual = manual if manual is not None else fpmod.manual_edits(edl, ask_plans())
    if mode is None:
        mode = "rerun" if segments or _fp_record() else "run"
    if mode not in fpmod.MODES:
        raise ValueError(f"mode must be one of {list(fpmod.MODES)}")
    locked = fpmod.resolve_keep(keep, segments, manual, mode)
    facts = facts if facts is not None else flow_facts()
    stages = {s["key"]: s for s in flowmod.compute(facts)["stages"]}
    ix = stages["index"]
    counts = _fp_index_counts(ix)
    index_needed = ix["state"] != "done" or counts["proxied"] < counts["clips"]
    ix_usd = float(facts["index"].get("pending_usd") or 0.0) if index_needed else 0.0
    n = counts["clips"]
    if index_needed:
        # The proposal waits for the words and one coarse look at every clip; previews
        # and close looks finish in the background (verdict 3, punch 5).
        todo = [f"hear {n - counts['listened']} of {n} clips" if counts["listened"] < n else "",
                f"look at {counts['look_need'] - counts['looked']}"
                if counts["looked"] < counts["look_need"] else ""]
        later = [f"{counts['close_need'] - counts['close']} close looks"
                 if counts["close"] < counts["close_need"] else "",
                 f"{n - counts['proxied']} previews" if counts["proxied"] < n else ""]
        will_ix = ("Index the rest: " + ", ".join(t for t in todo if t) + "."
                   if any(todo) else "Every clip is heard and looked at.")
        if any(later):
            will_ix += (" " + " and ".join(t for t in later if t)
                        + " finish in the background — the cut does not wait for them.")
    else:
        will_ix = "Nothing — every clip is heard, looked at and previewed."
    lock_set = set(locked)
    keep_rows = [r for r in manual if r.get("id") in lock_set]
    for i, s in enumerate(segments):
        if s.get("id") in lock_set and not any(r["id"] == s.get("id") for r in keep_rows):
            keep_rows.append(fpmod._row(s, i, []) | {"words": "kept on request"})
    keep_rows.sort(key=lambda r: r["pos"])
    replace_rows = [fpmod._row(s, i, []) | {"words": "may be replaced"}
                    for i, s in enumerate(segments) if s.get("id") not in lock_set]
    originate = not segments or (mode == "run" and not locked)
    cut_usd = float(ask_price("first" if originate else "full")["usd"])
    if not segments:
        will_cut = "Propose a first rough cut — nothing changes until you accept it."
    elif originate:
        will_cut = (f"Propose a new cut from scratch; it would replace all "
                    f"{len(segments)} shots of this cut if you accept it.")
    else:
        will_cut = (f"Propose a new cut that keeps {len(keep_rows)} of your shots exactly "
                    f"and may replace the other {len(replace_rows)} — nothing changes "
                    "until you accept it.")
    steps = [
        {"key": "index", "name": "Index", "will": will_ix, "needed": index_needed,
         "counts": counts, "cost": fpmod.cost_words(ix_usd)},
        {"key": "picks", "name": "Picks", "needed": True,
         "will": "Rank what the index found into picks (free, on this machine).",
         "cost": fpmod.cost_words(0.0)},
        {"key": "cut", "name": "Rough cut", "needed": True, "will": will_cut,
         "originate": originate, "cost": fpmod.cost_words(cut_usd)},
    ]
    return {"mode": mode, "steps": steps, "keep": keep_rows, "replace": replace_rows,
            "locked": locked, "cost": fpmod.cost_words(ix_usd + cut_usd),
            # verdict 4: the ranges the editor rejected — a run will not propose them again
            "rejected": [{k: r.get(k) for k in ("clip", "in", "out")} for r in fpmod.rejections(edl)],
            "produces": "a proposed rough cut you accept, discard or adjust"}


def firstpass_state() -> dict:
    """The first pass as the UI shows it — see `GET /api/firstpass`."""
    edl = read_edl()
    ensure_segment_ids(edl)
    facts = flow_facts()
    manual = fpmod.manual_edits(edl, ask_plans())
    # one consistent pair: a run that ends sets its state and saves its record under
    # this lock, so a poll between the two read a "running" record with no live run and
    # called it interrupted by a restart (a flake in test_firstpass)
    with FIRSTPASS_LOCK:
        live = _fp_live()
        rec = _fp_record()
    out: dict = {"manual_edits": manual, "last": rec, **config.cost_terms()}
    try:
        out["would"] = firstpass_plan(None, None, facts=facts, edl=edl, manual=manual)
    except ValueError as exc:                     # a cut with an id the keep cannot name
        out["would"] = {"error": str(exc)}
    out["label"] = "Re-run first pass" if rec else "Run first pass"
    if live:
        stage = live["stage"]
        stages = {s["key"]: s for s in flowmod.compute(facts)["stages"]}
        counts: dict = {"index": _fp_index_counts(stages["index"])}
        if live.get("picks") is not None:
            counts["picks"] = {"picks": live["picks"]}
        ask = ASKS.get(live.get("ask") or "")
        if ask is not None:
            snap = ask.snapshot()
            counts["cut"] = {"detail": snap.get("detail") or "", "state": snap.get("state")}
        out.update(state="running", job=live["id"], stage=stage, mode=live["mode"],
                   steps=live["steps"], counts=counts, started=live["started"])
        return out
    if rec is None:
        out["state"] = "never-run"
        return out
    if rec.get("state") in ("stopped", "failed", "running"):
        # A run that ended early is a state, never "not run yet" (verdict 3): when,
        # what the index had finished (the journal keeps it), and what a re-run does.
        stages = {s["key"]: s for s in flowmod.compute(facts)["stages"]}
        now_counts = _fp_index_counts(stages["index"])
        step = rec.get("step") or "index"
        out.update(step=step, counts={"index": now_counts},
                   finished=fpmod.finished_words(now_counts),
                   resumes=fpmod.resume_words(now_counts, step))
        if rec.get("state") == "stopped":
            out.update(state="stopped", when=rec.get("stopped"))
        elif rec.get("state") == "failed":
            out.update(state="failed", error=rec.get("error"), when=rec.get("finished"))
        else:                                     # a run the process lost (a restart)
            out.update(state="failed", error="the last run was interrupted (the server "
                       "restarted) — run it again", when=rec.get("started"))
        return out
    prop = rec.get("proposal") or {}
    waiting = ask_pending(STATE["edl"].stat().st_mtime)
    pending = bool(prop.get("job")) and bool(waiting) and waiting.get("job") == prop.get("job")
    answered = None
    for r in _ask_records():
        if r.get("job") == prop.get("job"):
            answered = (r.get("answered") or {}).get("answer")
            break
    # Edits *since the run*: a hand-edited shot the run already knew (and a re-run
    # locked) is not news; one trimmed, razored, moved or added after it is.
    then = {(r.get("id"), r.get("clip"), r.get("in"), r.get("out"))
            for r in rec.get("manual") or []}
    since = [r for r in manual if (r.get("id"), r.get("clip"), r.get("in"), r.get("out"))
             not in then]
    stale = []
    if since and not pending:
        stale.append(f"{len(since)} shot{'s' if len(since) != 1 else ''} edited by hand "
                     "since")
    # Footage added since the run (wave 12 IMPORT: imported, uploaded or dropped into the
    # folder): by name when the record kept the names, else by count. The board offers
    # them to a re-run ("3 new clips: add them to the cut?"), which says what it keeps.
    names = rec.get("clip_names")
    if isinstance(names, list):
        then_clips = set(names)
        fresh = [c for c in footage_clips() if c not in then_clips]
        n_new = len(fresh)
    else:
        fresh = []
        n_new = int(facts.get("clips") or 0) - int(rec.get("clips") or 0)
    if n_new > 0:
        stale.append(f"{n_new} new clip{'s' if n_new != 1 else ''} since the last run")
    out.update(state="stale-after-edits" if (since and not pending) else "done",
               when=rec.get("finished"), produced=rec.get("produced") or {},
               proposal={**prop, "pending": pending, "answer": answered}, stale=stale,
               edited_since=since, new_clips=fresh, new_count=max(0, n_new))
    return out


@app.get("/api/firstpass")
async def api_firstpass() -> JSONResponse:
    """Where the first pass stands, what a run would do, and what it costs.

    `state`: `never-run` · `running` (with `stage` index|picks|cut, `steps`, `counts` —
    the index's clips heard / looked at / previewed and a % computed from them only) ·
    `done` (`when`, `produced`, `proposal` {job, pending, answer}) · `stale-after-edits`
    (done, and the cut has shots edited by hand since — `stale` says what) · `failed`
    (`error`). Always: `label` (Run / Re-run first pass), `would` (the steps a run takes
    now with their `cost` words, `keep` — the shots a re-run locks — and `replace`),
    `manual_edits` (the editor's own shots and how), `backend` / `charged`. From the
    snapshot (`_snapped`); a running pass's Ask detail is merged in from memory."""
    return await snap_respond("firstpass", firstpass_payload, live=_fp_live_ask)


def _fp_live_ask() -> dict | None:
    """A running pass's Ask, as its counts show it: what changes while it thinks."""
    live = _fp_live()
    ask = ASKS.get((live or {}).get("ask") or "")
    if ask is None:
        return None
    snap = ask.snapshot()
    return {"detail": snap.get("detail") or "", "state": snap.get("state")}


def firstpass_payload() -> dict:
    out = _snapped("firstpass", firstpass_state)
    if out.get("state") == "running":
        cut = _fp_live_ask()
        if cut is not None:
            out = {**out, "counts": {**(out.get("counts") or {}), "cut": cut}}
    return out


@app.post("/api/firstpass")
async def api_firstpass_post(request: Request) -> JSONResponse:
    """Run (or re-run) the first pass: index what isn't done → picks → a proposed cut,
    as one job. Body: `mode` "run" | "rerun" (default: rerun when there is a cut),
    `keep` [shot ids] | "manual" | "none" (default on a re-run: "manual" — every shot
    trimmed, razored, moved or added by hand), `note` (optional direction). Returns
    `{job, mode, keep, would}`; the proposal lands as an Ask record (`/api/asks/latest`,
    `/api/ask/{job}`) whose plan carries `firstpass.diff` — per shot kept / replaced /
    new — and never writes the cut."""
    body = await request.json() if int(request.headers.get("content-length") or 0) else {}
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object")
    if not footage_clips():
        raise HTTPException(400, "no footage in this folder — nothing to pass over")
    with FIRSTPASS_LOCK:
        if _fp_live():
            raise HTTPException(409, "the first pass is already running")
        try:
            would = firstpass_plan(body.get("mode"), body.get("keep"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        refuse_past_cap("the first pass", float(would["cost"]["usd"]))
        job = uuid.uuid4().hex[:8]
        FIRSTPASS[job] = {
            "id": job, "state": "running", "mode": would["mode"], "keep": would["locked"],
            "note": str(body.get("note") or "").strip()[:600], "stage": "index",
            "started": time.time(), "ask": None, "picks": None,
            "steps": {k: {"state": "pending"} for k in ("index", "picks", "cut")},
            "scope": procscope.Scope(f"first pass {job}"), "index_job": None}
    threading.Thread(target=_firstpass_job, args=(job,), daemon=True).start()
    return JSONResponse({"job": job, "mode": would["mode"], "keep": would["locked"],
                         "would": would})


@app.post("/api/firstpass/cancel")
def api_firstpass_cancel() -> JSONResponse:
    """Stop the running first pass (the header control's ×), now: the tools it is
    running — the index's ffmpeg encodes, its contact-sheet reads, the `claude -p`
    writing the cut — are killed with their process groups, the way the export's
    cancel kills its render, and the state is `stopped` before this returns. What
    finished is kept (the journal), a stage that was killed goes back in the queue,
    and a proposal that lands after the stop is discarded, never offered."""
    with FIRSTPASS_LOCK:
        live = _fp_live()
        if live is None:
            raise HTTPException(409, "the first pass is not running")
        live["cancel"] = True
        live["state"] = "cancelled"
        _fp_step(live, live.get("stage") or "index", "cancelled")
        indexes = [x for x in INDEXES.values() if x["state"] not in progress.TERMINAL]
        for x in indexes:
            x["cancel"] = True
        rec = _fp_record() or {}
        if rec.get("job") == live["id"]:
            rec.update(state="stopped", stopped=time.time(),
                       step=live.get("stage") or "index")
            _fp_save(rec)
    scopes = [live["scope"]] + [INDEX_SCOPES[x["id"]] for x in indexes
                                if x["id"] in INDEX_SCOPES]
    killers = [threading.Thread(target=sc.kill, daemon=True) for sc in scopes]
    # and the preview encodes (verdict 4: "one ffmpeg (a preview build) kept running")
    killers.append(threading.Thread(target=stop_media, daemon=True))
    for t in killers:
        t.start()
    for t in killers:
        t.join(timeout=procscope.GRACE_S + 1.0)
    return JSONResponse({"job": live["id"], "stage": live.get("stage"), "stopped": True,
                         "state": "stopped"})


class _FpCancelled(Exception):
    """The editor stopped the first pass."""


def _fp_check(fp: dict) -> None:
    if fp.get("cancel"):
        raise _FpCancelled()


def firstpass_index(fp: dict) -> str | None:
    """The index step: join an index already running, or start one, in the coarse
    order (every clip heard and looked at first), and return as soon as the bin is
    coarse-ready — or the index ended, or the editor stopped the pass. Proxies and
    close looks are not the proposal's prerequisites: the index carries on with them
    in the background while the picks and the cut are made (verdict 3, punch 5)."""
    while True:
        running = next((x for x in INDEXES.values()
                        if x["state"] not in progress.TERMINAL), None)
        if running is None or not running.get("cancel") or fp.get("cancel"):
            break
        time.sleep(FIRSTPASS_POLL_S)     # a stopped index still unwinding: a new one
    if fp.get("cancel"):
        return None
    if running is not None and not running.get("cancel"):
        running["coarse"] = True
        if running.get("free"):              # started by an import: the pass needs looks
            running["free"] = False
            running["label"] = "Indexing the footage"
        # The running index read the clip list when it began (often the last pass's,
        # still building previews): footage imported since joins it before this pass
        # counts the bin as heard (wave 12 IMPORT — a re-run once cut without them).
        job = running["id"]
        want = index_ask_intake(running)
    else:
        job = index_entry()
        INDEXES[job]["coarse"] = True
        threading.Thread(target=_index_run, args=(job, ""), daemon=True).start()
        want = 0
    fp["index_job"] = job
    while True:
        e = INDEXES[job]
        if fp.get("cancel"):
            return job
        if e["state"] in progress.TERMINAL:
            if int(e.get("took") or 0) < want and not e.get("cancel"):
                # it ended before it took the new footage in: one more run for it
                job = index_entry()
                INDEXES[job]["coarse"] = True
                threading.Thread(target=_index_run, args=(job, ""), daemon=True).start()
                fp["index_job"] = job
                want = 0
                continue
            return job
        if e.get("coarse_ready") and int(e.get("took") or 0) >= want:
            return job
        time.sleep(FIRSTPASS_POLL_S)


def index_ask_intake(entry: progress.Job) -> int:
    """Ask a running index to take in the bin's clips again (footage imported since it
    began). Returns the number its `took` reaches once it has (`_index_walk`)."""
    entry["asked"] = int(entry.get("asked") or 0) + 1
    return entry["asked"]


def _fp_step(fp: dict, key: str, state: str, **extra) -> None:
    fp["steps"][key] = {"state": state, **extra}
    if state == "running":
        fp["stage"] = key


def _fp_discard(ask: str) -> None:
    """A proposal that landed after the Stop: answered "discard", never offered."""
    try:
        p = STATE["asks"] / f"{ask}.json"
        r = json.loads(p.read_text(encoding="utf-8"))
        r["answered"] = {"answer": "discard", "at": time.time(), "why": "cancelled"}
        p.write_text(json.dumps(r, indent=1), encoding="utf-8")
    except (OSError, ValueError):
        pass


def _firstpass_job(job: str) -> None:
    fp = FIRSTPASS[job]
    with procscope.active(fp["scope"]):     # the cut's `claude -p` dies with the Stop
        _firstpass_walk(job, fp)


def _firstpass_walk(job: str, fp: dict) -> None:
    rec = {"job": job, "mode": fp["mode"], "keep": fp["keep"], "state": "running",
           "started": fp["started"], "clips": len(footage_clips()),
           # which clips the run saw: what an import afterwards is new against
           "clip_names": footage_clips(),
           "cut": cut_name_of(read_edl(), STATE["edl"])}
    try:
        rec["manual"] = [{k: r[k] for k in ("id", "clip", "in", "out")}
                         for r in fpmod.manual_edits(read_edl(), ask_plans())]
    except (OSError, ValueError, KeyError):
        rec["manual"] = []
    with FIRSTPASS_LOCK:
        if not fp.get("cancel"):
            _fp_save(rec)
    try:
        # 1 · index — until every clip is heard and looked at; the rest (previews,
        # close looks) carries on in the background and is not waited for
        _fp_step(fp, "index", "running")
        stages = {s["key"]: s for s in flowmod.compute(flow_facts())["stages"]}
        counts = _fp_index_counts(stages["index"])
        if stages["index"]["state"] != "done" or counts["proxied"] < counts["clips"]:
            ix_job = firstpass_index(fp)
            _fp_check(fp)
            stages = {s["key"]: s for s in flowmod.compute(flow_facts())["stages"]}
            counts = _fp_index_counts(stages["index"])
            ix = INDEXES.get(ix_job) or {}
            if ix.get("state") == "failed":
                raise RuntimeError(f"the index failed: {ix.get('detail')}")
            _fp_step(fp, "index", "done", job=ix_job, counts=counts,
                     background=ix.get("state") not in progress.TERMINAL)
        else:
            _fp_step(fp, "index", "skipped", why="already complete", counts=counts)
        if not counts["listened"]:
            raise RuntimeError("no clip has been heard, so there is nothing to cut from")

        # 2 · picks — the ranked events, then the floor's picks from them
        _fp_step(fp, "picks", "running")
        try:
            if STATE.get("visual") is not None and STATE["visual"].is_dir():
                rebuild_events()
        except (OSError, ValueError) as exc:
            print(f"  !! first pass: events not rebuilt: {exc}", flush=True)
        rows = bin_picks()
        fp["picks"] = len(rows)
        _fp_step(fp, "picks", "done", picks=len(rows))
        _fp_check(fp)

        # 3 · the proposed cut — the Ask's own call, locks enforced, notes checked
        _fp_step(fp, "cut", "running")
        clips, payload = _ask_clips(None, drop_junk=True)
        if not clips:
            raise RuntimeError("no analysed clips to cut from")
        edl = read_edl()
        ensure_segment_ids(edl)
        segments = fpmod._segments(edl)
        manual = fpmod.manual_edits(edl, ask_plans())
        locked = [k for k in fp["keep"] if any(s.get("id") == k for s in segments)]
        originate = not segments or (fp["mode"] == "run" and not locked)
        # verdict 4: the ranges the editor rejected in a review — not proposed again
        rejected = fpmod.rejections(edl)
        note = (fpmod.plan_note(len(locked), fp["mode"], fp["note"], rejected)
                if not originate else
                "\n\n".join(x for x in ((fp["note"] or "").strip(),
                                         fpmod.rejected_brief(rejected)) if x))

        def finish(plan: dict) -> dict:
            plan = dict(plan)
            plan["segments"], left = fpmod.drop_rejected(plan["segments"], segments,
                                                         rejected)
            if segments and locked:
                plan["segments"] = fpmod.enforce_locks(plan["segments"], segments, locked)
            if left:
                plan["notes"] = "\n\n".join(x for x in (
                    str(plan.get("notes") or "").strip(),
                    "\n".join(fpmod.rejected_lines(left))) if x)
            plan["firstpass"] = {
                "job": job, "mode": fp["mode"], "keep": locked,
                "manual": [r for r in manual if r.get("id") in set(locked)],
                "rejected": left,
                "diff": fpmod.rerun_diff(segments, plan["segments"], locked, manual)}
            return plan

        ask = uuid.uuid4().hex[:8]
        ASKS[ask] = progress.Job(
            "ask", "First pass — proposing the cut" if originate
            else "First pass — re-cutting around your edits", id=ask, state="estimating",
            plan=None, code=0, detail="estimating how long this will take",
            ask_kind="first pass", estimate=None, firstpass=job)
        fp["ask"] = ask
        _fp_check(fp)
        target = payload["target"]
        _ask_job(ask, segments, clips, payload.get("story", ""), note,
                 (float(target[0]), float(target[1])), payload["events"], None,
                 locked=locked, finish=finish, originate=originate)
        entry = ASKS[ask]
        with FIRSTPASS_LOCK:
            if fp.get("cancel"):
                # stopped while the model wrote: the proposal is discarded, never offered
                _fp_discard(ask)
                entry.finish("cancelled", detail="the first pass was stopped")
                raise _FpCancelled()
            if entry["state"] != "done":
                raise RuntimeError(f"the cut failed: {entry.get('detail')}")
            plan = entry["plan"] or {}
            diff = (plan.get("firstpass") or {}).get("diff") or {}
            _fp_step(fp, "cut", "done", ask=ask, shots=len(plan.get("segments") or []),
                     counts=diff.get("counts"))
            rec.update(state="done", finished=time.time(),
                       proposal={"job": ask, "shots": len(plan.get("segments") or []),
                                 "counts": diff.get("counts"),
                                 "sentence": diff.get("sentence"),
                                 "notes_ok": (plan.get("notes_check") or {}).get("ok", True)},
                       produced={"index": fp["steps"]["index"].get("counts"),
                                 "picks": fp["picks"],
                                 "cut": {"shots": len(plan.get("segments") or []),
                                         "length_s": round(sum(edits.dur(s) for s in
                                                               plan.get("segments") or []), 2)}})
            fp["state"] = "done"
            _fp_save(rec)
    except _FpCancelled:
        pass
    except Exception as exc:  # noqa: BLE001 — deliberately total: the job must end
        with FIRSTPASS_LOCK:
            if not fp.get("cancel"):
                step = fp.get("stage") or "index"
                _fp_step(fp, step, "failed", error=str(exc)[:300])
                rec.update(state="failed", finished=time.time(), error=str(exc)[:300],
                           step=step)
                fp["state"] = "failed"
                fp["error"] = str(exc)[:300]
                _fp_save(rec)
    finally:
        with FIRSTPASS_LOCK:
            newer = any(x is not fp and x["started"] > fp["started"]
                         for x in FIRSTPASS.values())
            if fp.get("cancel") and not newer:
                # The Stop recorded itself; a run stopped before its own record was
                # written still leaves one that says it stopped, never "not run yet".
                cur = _fp_record() or {}
                if cur.get("job") != job or cur.get("state") != "stopped":
                    rec.update(state="stopped", stopped=time.time(),
                               step=fp.get("stage") or "index")
                    _fp_save(rec)
                fp["state"] = "cancelled"


# ---------------------------------------------------------------- analysis


def analyze_cmd(skip: list[str], force: bool) -> list[str]:
    """The audio pass, as a command. A function so the tests can replace it: running
    faster-whisper for real would mean a GPU, a 3GB model download and minutes per
    suite — none of which says anything about whether the job plumbing works."""
    cmd = ["uv", "run", "--quiet", str(TOOLS / "audio_analyze.py"),
           str(tool_footage()), "-o", str(STATE["sidecars"])]
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
            # The visual pass's calls happen in this child process, so its CLI
            # failures never reach this process's `inference` — read them off the log.
            # Error lines only: a sheet's own words ("log in", "blocked") must not.
            if re.search(r"error|failed|claude CLI", text, re.I):
                inference.note_problem(text)
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

        with procscope.active(procscope.Scope(f"proxies {job}", background=True)):
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
    cmd = ["uv", "run", "--quiet", str(TOOLS / "visual_pass.py"), str(tool_footage()),
           "-o", str(STATE["visual"]), "--orient", STATE["orient"],
           "--interval", f"{look_interval():g}"]
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
            "--orient", "auto", "--role", FINE_ROLE, "--windows", str(windows)]


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
        # Only the clips never looked at closely — the ones the price counted. The scan
        # now puts a clip's unaudited claims first and skips what was read, so scanning
        # an audited clip again would buy fresh windows the button never priced; that
        # is the audit's job (/api/visual/audit), with its own price.
        closely = fine_stems()
        stems = sorted(Path(c).stem for c in footage_clips()
                       if Path(c).stem not in closely)
        entry.note("scanning for motion — free, no model calls")
        rc = (_run_counted(entry, scan_cmd(stems, windows, windows_per_clip),
                           lambda: len(fine_stems()), key="fine_done", append=True)
              if stems else 0)
        entry.complete("scan")
        if rc == 0 and stems and windows.exists():
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
    sheets = sum(sheets_for(c) for c in clips if Path(c).stem in wanted)
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


def audit_cmd(windows: Path) -> list[str]:
    """The audit's close look — the same tool and settings as the second stage, a
    function of its own so the tests can see exactly which windows it was handed."""
    return fine_cmd(windows)


def _audit_job(job: str, windows: Path, stems: set[str]) -> None:
    """Close look at the planned windows, then the rank. Nothing else runs: the coarse
    pass and the scan have both happened, and this is the spend on what they claimed."""
    entry = VISUALS[job]
    entry["state"] = "running"
    entry["stage"] = "closer"
    before = {s: len(load_fine(s).get("windows_read") or []) for s in stems}

    def audited() -> int:
        return sum(1 for s in stems
                   if len(load_fine(s).get("windows_read") or []) > before[s])

    rc = _run_counted(
        entry, audit_cmd(windows), audited, key="fine_done",
        on_line=lambda line: _visual_note(entry, line, key="fine_done",
                                          total=len(stems), verb="audited"),
        on_count=lambda n: entry.advance(
            "closer", n / max(1, len(stems)),
            detail=_visual_note(entry, key="fine_done", total=len(stems),
                                verb="audited")))
    windows.unlink(missing_ok=True)
    entry.complete("closer")
    entry["stage"] = "ranking"
    entry.note("ranking what was seen")
    entry["events"] = rebuild_events()
    entry.complete("rank")
    if rc != 0:
        # The windows that were read are on disk and already ranked; what failed is
        # the rest, and it is still unaudited, so the button offers it again.
        entry.finish("failed", detail="the audit did not finish — see the log; "
                                      "what was read is kept and ranked")
        return
    entry["stage"] = "done"
    entry.finish("done", detail=f"{entry['claims']} claims audited, "
                                f"{entry['events']} events ranked")


@app.post("/api/visual/audit")
async def api_visual_audit(request: Request) -> JSONResponse:
    """Audit what the sheets claim — the close look on the bin's top unaudited claims.

    R10's first follow-up and the biggest lever left on the visual side (HANDOFF roadmap
    item 1): Killington's events file has no `confirmed` event at all, because the close
    look's windows went to motion peaks and never to the claims. This spends windows on
    exactly the claims that rank highest *because* nobody checked them, then rebuilds
    the rank. `{"dry_run": true}` prices it without spending — the board's button shows
    that price and the editor clicks; nothing here runs on the app's own initiative.
    """
    body = await request.json()
    n = max(1, min(40, int(body.get("n", AUDIT_CLAIMS))))
    rebuild_events()                     # free, and the plan must see what is on disk
    plan = audit_plan(events.load(STATE["visual"]), n)
    price = audit_price(plan)
    out = {**price, "n": n, "plan": plan}
    if body.get("dry_run"):
        return JSONResponse(out)
    if any(v["state"] not in progress.TERMINAL for v in VISUALS.values()):
        raise HTTPException(409, "a visual pass is already running")
    if not plan:
        raise HTTPException(400, "nothing to audit — every hot claim has had a close look")
    if _over_budget(price["projected_usd"]):
        raise HTTPException(409, f"the audit (~${price['projected_usd']:.2f}) would pass "
                                 f"{cap_words()}")
    job = uuid.uuid4().hex[:8]
    by_stem: dict[str, list[list[float]]] = {}
    for w in plan:
        by_stem.setdefault(Path(w["clip"]).stem.upper(), []).append([w["start"], w["end"]])
    windows = STATE["work"] / f"audit_{job}.json"
    windows.parent.mkdir(parents=True, exist_ok=True)
    windows.write_text(json.dumps(by_stem, indent=1), encoding="utf-8")
    stems = {Path(w["clip"]).stem for w in plan}
    VISUALS[job] = progress.Job(
        "visual", "Auditing the claims", id=job, stage="closer", log="",
        total=len(stems), done=0, fine_done=0, fine_total=len(stems), events=0,
        fine=True, sheets=len(plan), claims=price["claims"], now="",
        detail=f"{price['windows']} window{'' if price['windows'] == 1 else 's'} "
               f"over {price['claims']} claim{'' if price['claims'] == 1 else 's'}")
    VISUALS[job].set_estimate(
        price["eta_s"],
        [progress.milestone("closer", "a closer look at the claims",
                            max(1.0, len(plan) * FINE_WINDOW_READ_S)),
         progress.milestone("rank", "ranking what was seen", 2.0)],
        source="measured")
    threading.Thread(target=_audit_job, args=(job, windows, stems), daemon=True).start()
    return JSONResponse({**out, "job": job})


@app.get("/api/visual/{job}")
def api_visual_status(job: str) -> JSONResponse:
    if job not in VISUALS:
        raise HTTPException(404, "no such job")
    return JSONResponse(VISUALS[job].snapshot())


# ---------------------------------------------------------------- how the agent sees (M15)
#
# Karl, 2026-10-03 (#4): *"make it clearer how the videos are indexed by the agent (e.g.
# showing granularity), and make it easier to run deeper keyframe-based analysis."*
# Two halves. **Coverage** is free and read from the files only: for one clip, which
# seconds each layer of the index looked at, how densely, with which model (where the
# sidecar recorded it), and what each concluded — `deep.js` draws it as a strip of lanes.
# **The deep look** (`roughcut/deep.py`) is priced: keyframes where the picture changes,
# individual frames read in order on the deep tier, beats marked seen or inferred, at
# most one follow-up for frames it asks for. One at a time, priced by a dry run, refused
# past the budget cap, and it never re-buys a span already read.


def deep_frames_dir(stem: str) -> Path:
    """Per bin, like the proxies the frames are cut from — two bins can share a stem."""
    return STATE["work"] / "deep" / STATE["footage"].name / stem


def _footage_clip(clip: str) -> str:
    want = Path(str(clip)).stem.upper()
    for c in footage_clips():
        if Path(c).stem.upper() == want:
            return c
    raise HTTPException(404, f"no clip {clip} in this bin")


def _asr_model() -> str | None:
    """The ASR model, from the audio pass's own index — the sidecars do not carry it."""
    try:
        d = json.loads((STATE["sidecars"] / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return ((d.get("asr") or {}) if isinstance(d, dict) else {}).get("model")


def clip_coverage(clip: str) -> dict:
    stem = Path(clip).stem
    vp: Path = STATE["visual"] / f"{stem}.visual.json"
    try:
        coarse = json.loads(vp.read_text(encoding="utf-8")) if vp.exists() else {}
    except (OSError, ValueError):
        coarse = {}
    fine = load_fine(stem)
    deep = deepmod.load(STATE["visual"], stem)
    motion = events.motion_cache(STATE["visual"], stem)
    audio = load_sidecar(clip)
    track = events.excitement(motion, (audio.get("tracks") or {}).get("onset") or [])
    moments = events.rank_clip(clip, coarse, fine, track, events.MOTION_HZ, deep)
    return deepmod.coverage(clip, duration=clip_duration(clip), audio=audio,
                            coarse=coarse, fine=fine, deep=deep, motion=motion,
                            hz=events.MOTION_HZ, moments=moments,
                            asr_model=_asr_model())


@app.get("/api/coverage")
def api_coverage_bin() -> JSONResponse:
    """Every clip's coverage in compact form, for the open screen's cards."""
    return JSONResponse({"clips": {c: deepmod.summary(clip_coverage(c))
                                   for c in footage_clips()}})


@app.get("/api/coverage/{clip}")
def api_coverage(clip: str) -> JSONResponse:
    """Which seconds of one clip the machine looked at, how densely, and what it said."""
    return JSONResponse(clip_coverage(_footage_clip(clip)))


def deep_plan(clip: str, start: float, end: float, force: bool = False) -> dict:
    """What a deep look at [start, end] would read and cost — free, nothing is cut.

    With the clip's motion track cached the frames are chosen exactly as the run will
    choose them; without it the run measures the track first (free) and the price
    assumes the cap, which is the most it can be."""
    stem = Path(clip).stem
    s, e = deepmod.clamp_span(start, end, clip_duration(clip))
    have = deepmod.covering(deepmod.load(STATE["visual"], stem), s, e)
    motion = events.motion_cache(STATE["visual"], stem)
    frames = deepmod.keyframes(motion, s, e, hz=events.MOTION_HZ) if motion else []
    out = {"clip": clip, "start": s, "end": e, "requested": [start, end],
           "capped": (end - start) - (e - s) > 0.01,
           "max_span_s": deepmod.MAX_SPAN_S,
           "frames_plan": [f["t"] for f in frames],
           "motion": "cached" if motion else "measured first — free",
           **deepmod.quote(len(frames) if motion else deepmod.FRAME_CAP),
           **config.cost_terms()}
    out["cached"] = bool(have) and not force
    if out["cached"]:
        out.update(projected_usd=0.0, max_usd=0.0, calls=0, max_calls=0, eta_s=0.0,
                   read={"start": have["start"], "end": have["end"]})
    return out


def _num_field(body: dict, key: str) -> float:
    try:
        return float(body[key])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, f"'{key}' must be a number of seconds") from None


@app.post("/api/deep/quote")
async def api_deep_quote(request: Request) -> JSONResponse:
    """Prices for many spans in one request — the seen tab prices every row's button."""
    body = await request.json()
    out = []
    for sp in (body.get("spans") or [])[:80]:
        try:
            clip = _footage_clip(sp.get("clip", ""))
            out.append(deep_plan(clip, _num_field(sp, "start"), _num_field(sp, "end")))
        except HTTPException as exc:
            out.append({"error": exc.detail, **{k: sp.get(k) for k in
                                                ("clip", "start", "end")}})
    return JSONResponse({"quotes": out})


def _deep_job(job: str, clip: str, start: float, end: float) -> None:
    entry = DEEPS[job]
    stem = Path(clip).stem
    proxy: Path = STATE["proxy_dir"] / f"{stem}.mp4"

    def on_step(key: str, detail: str) -> None:
        if key in ("frames", "read"):
            entry.complete(key)
        entry.note(detail)

    try:
        motion = events.motion_cache(STATE["visual"], stem)
        if not motion:
            entry.note("measuring the motion — free, no model call")
            motion = events.motion_track(proxy)
            events.write_motion_cache(STATE["visual"], stem, clip, motion)
        entry.complete("motion")
        record = deepmod.look(
            proxy, clip, start, end, frames_dir=deep_frames_dir(stem), mafd=motion,
            hz=events.MOTION_HZ, may_follow_up=lambda usd: not _over_budget(usd),
            on_step=on_step)
    except inference.BudgetExceeded as exc:
        entry.update(code=429)
        entry.finish("failed", detail=str(exc))
        return
    except (inference.InferenceError, RuntimeError, ValueError, OSError) as exc:
        entry.update(code=502)
        entry.finish("failed", detail=f"the deep look failed: {str(exc)[:300]}")
        return
    deepmod.store(STATE["visual"], stem, clip, record)
    entry["record"] = {"start": record["start"], "end": record["end"]}
    entry.note("ranking what was seen")
    entry["events"] = rebuild_events()
    entry.complete("rank")
    inferred = sum(1 for b in record["beats"] if b["basis"] == "inferred")
    more = (f" — read again with {len(record['asked'])} frames it asked for"
            if record["followup"] else "")
    entry.finish("done", detail=f"{len(record['beats'])} beats ({inferred} inferred), "
                                f"{len(record['events'])} events, "
                                f"${record['projected_usd']:.2f}{more}")


@app.post("/api/deep")
async def api_deep(request: Request) -> JSONResponse:
    """Look deeper at a span of one clip. `{"dry_run": true}` prices it and spends nothing;
    the board's button carries that price and nothing runs without the click. A span
    already read comes back as `cached` and costs nothing (`force` re-reads it)."""
    body = await request.json()
    clip = _footage_clip(body.get("clip", ""))
    plan = deep_plan(clip, _num_field(body, "start"), _num_field(body, "end"),
                     force=bool(body.get("force")))
    if body.get("dry_run") or plan["cached"]:
        return JSONResponse({**plan, "job": None})
    if any(d["state"] not in progress.TERMINAL for d in DEEPS.values()):
        raise HTTPException(409, "a deep look is already running — one at a time")
    if not (STATE["proxy_dir"] / f"{Path(clip).stem}.mp4").exists():
        raise HTTPException(400, f"{clip} has no preview yet — the frames come from it")
    if _over_budget(plan["max_usd"]):
        raise HTTPException(409, f"the deep look (up to ~${plan['max_usd']:.2f}) would "
                                 f"pass {cap_words()}")
    job = uuid.uuid4().hex[:8]
    DEEPS[job] = progress.Job(
        "deep", f"Looking deeper · {Path(clip).stem} {plan['start']:.1f}–{plan['end']:.1f} s",
        id=job, clip=clip, start=plan["start"], end=plan["end"], code=0, events=0,
        detail=f"cutting up to {plan['frames']} frames")
    DEEPS[job].set_estimate(
        plan["eta_s"],
        [progress.milestone("motion", "the motion track", 1.0),
         progress.milestone("frames", "cutting the keyframes", 4.0),
         progress.milestone("read", "reading the frames in order", plan["eta_s"]),
         progress.milestone("rank", "ranking what was seen", 1.0)],
        source="measured")
    threading.Thread(target=_deep_job, args=(job, clip, plan["start"], plan["end"]),
                     daemon=True).start()
    return JSONResponse({**plan, "job": job})


@app.get("/api/deep/{clip}")
def api_deep_get(clip: str) -> JSONResponse:
    """A clip's deep looks, whole — beats, events, camera, frames — and any running one."""
    clip = _footage_clip(clip)
    stem = Path(clip).stem
    d = deepmod.load(STATE["visual"], stem)
    running = next((j for j in DEEPS.values() if j.get("clip") == clip
                    and j["state"] not in progress.TERMINAL), None)
    return JSONResponse({"clip": clip, "spans": d.get("spans") or [],
                         "frames_url": f"/media/deep/{stem}/",
                         "job": running["id"] if running else None})


_DEEP_FRAME = re.compile(r"^t\d{4}\.\d{2}\.jpg$")


@app.get("/media/deep/{stem}/{name}")
def deep_frame(stem: str, name: str) -> Response:
    if not _DEEP_FRAME.match(name) or "/" in stem or "\\" in stem or stem.startswith("."):
        raise HTTPException(404, "no such frame")
    p = deep_frames_dir(stem) / name
    if not p.is_file():
        raise HTTPException(404, "no such frame")
    return FileResponse(p, media_type="image/jpeg")


# ---------------------------------------------------------------- themes
#
# Listen first, then propose (design §2, INTAKE I5.2): after the free audio pass one
# cheap call reads the transcripts and proposes what the film might be about, as chips
# with clip counts. The editor confirms, edits, dictates, or ignores; what they keep is
# written to the EDL as `themes` (and `names`, which seed dictation). Themes rank picks
# and never change what the sheets see, so changing them later costs nothing.

THEMES: dict[str, progress.Job] = {}
THEMES_ETA_S = 30.0


def _themes_job(job: str, clips: dict, story: str) -> None:
    entry = THEMES[job]

    def on_partial(kind: str, text: str) -> None:
        if kind == "thinking":
            entry.complete("read")
            entry.note("reading the transcripts")
            return
        entry.complete("think")
        n = text.count('"theme"')
        entry.advance("write", n / 6.0, detail=f"{n} theme{'s' if n != 1 else ''} so far")

    try:
        proposal = themes.propose(clips, story, on_partial=on_partial)
    except inference.BudgetExceeded as exc:
        entry.update(code=429)
        entry.finish("failed", detail=str(exc))
        return
    except (inference.InferenceError, ValueError) as exc:
        entry.update(code=502)
        entry.finish("failed", detail=str(exc))
        return
    entry["proposal"] = proposal
    # On disk too: a proposal cost money, and a server restart must not lose it before
    # the editor has answered it.
    try:
        proposal_path().parent.mkdir(parents=True, exist_ok=True)
        proposal_path().write_text(json.dumps({"id": job, "proposal": proposal}, indent=1),
                                   encoding="utf-8")
    except OSError:
        pass
    entry.complete("write")
    n = len(proposal["themes"])
    entry.finish("done", detail=f"{n} theme{'s' if n != 1 else ''} proposed")


@app.get("/api/themes")
def api_themes() -> JSONResponse:
    """What the EDL says the film is about, and what proposing more would cost."""
    edl = read_edl()
    clips, _payload = _ask_clips()
    running = next((t for t in THEMES.values() if t["state"] not in progress.TERMINAL), None)
    # The last finished proposal, so a page reloaded after the call answered shows the
    # chips instead of asking to pay again. The open screen asked for this.
    done = [t for t in THEMES.values() if t["state"] == "done" and t.get("proposal")]
    last = done[-1] if done else None
    if last is None and proposal_path().exists():        # from before a restart
        try:
            last = json.loads(proposal_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last = None
    return JSONResponse({
        "themes": [t for t in (edl.get("themes") or []) if isinstance(t, str)],
        "names": [n for n in (edl.get("names") or []) if isinstance(n, str)],
        "story": edl.get("story", ""),
        "projected_usd": themes.projected_usd(clips) if clips else None,
        "analysed": len(clips), "job": running["id"] if running else None,
        "last": {"id": last["id"], "proposal": last["proposal"]} if last else None,
        "dictation": dictate.available(),
        **config.cost_terms(),
    })


@app.post("/api/themes/propose")
async def api_themes_propose(request: Request) -> JSONResponse:
    """One judge-role call over the transcripts, as a job with a price shown first."""
    if any(t["state"] not in progress.TERMINAL for t in THEMES.values()):
        raise HTTPException(409, "a themes proposal is already running")
    clips, payload = _ask_clips()
    if not clips:
        raise HTTPException(400, "no analysed clips yet — run the audio pass first")
    body = await request.json() if int(request.headers.get("content-length") or 0) else {}
    story = str(body.get("story") or payload.get("story") or "")
    refuse_past_cap("proposing themes", themes.projected_usd(clips))
    job = uuid.uuid4().hex[:8]
    THEMES[job] = progress.Job(
        "themes", "Proposing themes from the transcripts", id=job, state="running",
        proposal=None, code=0, detail="reading the transcripts")
    THEMES[job].set_estimate(
        THEMES_ETA_S,
        [progress.milestone("read", "reading the transcripts", 0.2),
         progress.milestone("think", "finding what recurs", 0.5),
         progress.milestone("write", "writing the themes", 0.3)],
        source="measured")
    threading.Thread(target=_themes_job, args=(job, clips, story), daemon=True).start()
    return JSONResponse({"job": job})


@app.put("/api/themes")
async def api_themes_put(request: Request) -> JSONResponse:
    """The editor's word: the themes and names to keep. Written to the EDL; picks and the
    priority score read them from there. An empty list is a valid answer."""
    body = await request.json()
    edl = read_edl()
    for key, cap in (("themes", themes.MAX_THEMES), ("names", themes.MAX_NAMES)):
        if key in body:
            raw = body[key]
            if not isinstance(raw, list):
                raise HTTPException(400, f"{key} must be a list of strings")
            clean: list[str] = []
            for x in raw:
                s = str(x).strip()[:60]
                if s and s.lower() not in {c.lower() for c in clean}:
                    clean.append(s)
            edl[key] = clean[:cap]
    if "story" in body:
        edl["story"] = str(body["story"])
    write_edl(edl)
    if "themes" in body:
        _forget_proposals()          # kept (or emptied): the proposal has been answered
    return JSONResponse({"ok": True, "themes": edl.get("themes", []),
                         "names": edl.get("names", [])})


def _forget_proposals() -> None:
    """Drop finished proposal jobs so `GET /api/themes` stops offering them as `last`.
    Keep and Discard are the two answers to a proposal; either one ends it."""
    for k in [k for k, t in THEMES.items() if t["state"] in progress.TERMINAL]:
        del THEMES[k]
    proposal_path().unlink(missing_ok=True)


def proposal_path() -> Path:
    return STATE["work"] / "themes" / f"{STATE['footage'].name}.proposal.json"


@app.post("/api/themes/discard")
def api_themes_discard() -> JSONResponse:
    """The editor's other answer: the proposal is thrown away, nothing written, and a
    reload no longer brings it back."""
    _forget_proposals()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------- the index
#
# Decision 3 (docs/INTAKE.md): the index runs unattended and resumable, in a priority
# order, and releases clips whole. `roughcut/journal.py` is the plan; this is the hand
# that runs it — one thread walking `journal.next()` and running each (clip, stage) with
# the tools the two old buttons already drove, reporting back with start / finish / fail.
# Killing the server mid-stage costs that stage: the next POST /api/index reconciles the
# journal against the sidecars on disk and carries on. Adding footage is the same path.

# ------------------------------------------------------------------ settings
# The two knobs the design's settings drawer names (cutting-room-floor §3, Fig. 1): the
# budget cap and the index's workers. Kept under --work, not in the EDL — they are how
# this machine runs, not what the film is — and the environment still wins for the cap,
# so a production deployment can pin it. The cap is optional and off by default (Karl,
# INTAKE M16 decision 7: "Do not have a cap … 'no cap' … on by default"); when one is
# set it caps what *this project* has spent (`project_spent`), not this process — and it
# is kept for this project alone, by the bin's name as its spend file and its journal
# are: the drawer says "cap this project at $__", and one cap for every bin capped
# copper the moment Killington was capped.

SETTINGS_FILE = "settings.json"
CAPS_KEY = "budget_usd_by_bin"          # bin name → that project's cap
WORKER_RANGE = (1, 8)


def settings_path() -> Path:
    return STATE["work"] / SETTINGS_FILE


def load_settings() -> dict:
    try:
        d = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def save_settings(d: dict) -> None:
    settings_path().parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(settings_path())


def _bin_name() -> str | None:
    footage = STATE.get("footage")
    return footage.name if footage else None


def saved_cap(saved: dict | None = None) -> float | None:
    """This project's cap as the settings drawer saved it, or None."""
    caps = (load_settings() if saved is None else saved).get(CAPS_KEY)
    raw = caps.get(_bin_name()) if isinstance(caps, dict) else None
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def budget_cap() -> float | None:
    """The cap on this project's spend that the index, the audit, the deep look and
    every model call honour: the environment if it is set (the production pin), else
    this project's saved cap, else None — no cap, the default."""
    env = config.budget_usd()
    if env is not None:
        return env
    return saved_cap()


def settings_payload() -> dict:
    saved = load_settings()
    return {
        "budget_usd": budget_cap(),
        "spent_usd": project_spent(),
        "workers": {**journal.DEFAULT_WORKERS, **(saved.get("workers") or {})},
        "defaults": {"budget_usd": None, "workers": dict(journal.DEFAULT_WORKERS)},
        "source": {"budget_usd": ("env" if os.environ.get("ROUGHCUT_BUDGET_USD")
                                  else "settings" if saved_cap(saved) is not None
                                  else "default")},
    }


@app.get("/api/settings")
def api_settings() -> JSONResponse:
    return JSONResponse(settings_payload())


@app.put("/api/settings")
async def api_settings_put(request: Request) -> JSONResponse:
    """Validated like everything else that changes how money is spent: a cap must be
    positive (null is no cap), a worker count 1–8 on a stage the journal knows. Workers
    apply at the next index run; the cap applies at the next priced stage or call."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "expected an object")
    saved = load_settings()
    # The drawer before M16 kept one cap for every bin and wrote it on every Save, the
    # old $15 default included; it is not read (no cap by default, decision 7), and the
    # next save drops it so settings.json does not carry a cap that looks live.
    saved.pop("budget_usd", None)
    if "budget_usd" in body:
        caps = saved.get(CAPS_KEY)
        caps = dict(caps) if isinstance(caps, dict) else {}
        if body["budget_usd"] is None:
            caps.pop(_bin_name(), None)            # no cap: the default, so nothing kept
        else:
            try:
                cap = float(body["budget_usd"])
            except (TypeError, ValueError):
                raise HTTPException(400, "budget_usd must be a number, or null for no cap")
            if not (math.isfinite(cap) and cap > 0):
                raise HTTPException(400, "budget_usd must be positive, or null for no cap")
            caps[_bin_name()] = round(cap, 2)      # this project's, not every bin's
        if caps:
            saved[CAPS_KEY] = caps
        else:
            saved.pop(CAPS_KEY, None)
    if "workers" in body:
        raw = body["workers"]
        if not isinstance(raw, dict):
            raise HTTPException(400, "workers must be an object of stage: count")
        clean = dict(saved.get("workers") or {})
        for stage, n in raw.items():
            if stage not in journal.DEFAULT_WORKERS:
                raise HTTPException(400, f"unknown stage {stage!r}; "
                                         f"stages are {sorted(journal.DEFAULT_WORKERS)}")
            try:
                n = int(n)
            except (TypeError, ValueError):
                raise HTTPException(400, f"workers.{stage} must be an integer")
            if not WORKER_RANGE[0] <= n <= WORKER_RANGE[1]:
                raise HTTPException(400, f"workers.{stage} must be between "
                                         f"{WORKER_RANGE[0]} and {WORKER_RANGE[1]}")
            clean[stage] = n
        saved["workers"] = clean
    save_settings(saved)
    return JSONResponse(settings_payload())


INDEXES: dict[str, progress.Job] = {}
# A free index (one an import starts, wave 12 IMPORT): `Journal.next`'s per-call worker
# limits with the sheet pool — the look and the close look, the priced stages — at 0.
# Nothing is written to the journal, so the next index the editor starts is priced as
# ever and a restart forgets the hold.
FREE_ONLY = {"sheet": 0}


def journal_path() -> Path:
    return STATE["work"] / "index" / f"{STATE['footage'].name}.json"


def load_journal() -> journal.Journal:
    return journal.Journal.load(journal_path(), bin=STATE["footage"].name)


def index_files() -> dict[str, dict[str, bool]]:
    """What exists on disk per clip, for `journal.reconcile` — files are the truth.

    `picks` are derived, never written (INTAKE Discovered): they count as done when the
    events file is at least as new as everything it was built from.
    """
    events_file = STATE["visual"] / events.EVENTS_FILE
    events_at = events_file.stat().st_mtime if events_file.exists() else None
    out: dict[str, dict[str, bool]] = {}
    for clip in footage_clips():
        stem = Path(clip).stem
        vis = STATE["visual"] / f"{stem}.visual.json"
        fine = STATE["visual"] / f"{stem}.fine.json"
        newest = max((p.stat().st_mtime for p in (vis, fine) if p.exists()), default=None)
        # No `probe` key: nothing on disk records a probe, so the journal's own word
        # is the truth for it — reporting False would re-queue it on every restart.
        out[clip] = {
            "telemetry": (telemetry_dir() / f"{stem.upper()}.telemetry.json").exists(),
            "asr": stem in analysed_stems(),
            "proxy": (STATE["proxy_dir"] / f"{stem}.mp4").exists(),
            "look": vis.exists(),
            "close": fine.exists(),
            "picks": bool(events_at and newest and events_at >= newest),
        }
    return out


def index_facts(clip: str) -> dict:
    """The free-stage facts the priority score reads, from the audio sidecar."""
    d = load_sidecar(clip)
    transcript = d.get("transcript") or []
    words = (d.get("summary") or {}).get("n_words")
    if words is None:
        words = sum(len(u.get("words") or str(u.get("text", "")).split())
                    for u in transcript)
    edl = read_edl()
    # the sentence's words count as theme hits (INTAKE M16 decision 8)
    themes = picks.brief_tags(edl.get("story"), edl.get("themes"))
    text = " ".join(str(u.get("text", "")) for u in transcript)
    hits = sum(1 for t in themes if find._tokens(t) and find._matched(find._tokens(t), text))
    tele = telemetry_peaks(clip)
    return {"words": int(words or 0), "candidates": len(d.get("candidates") or []),
            # R11: loose freefall runs and > 5 g peaks, never 3 g — the least-weighted term
            "telemetry_peaks": len(tele["peaks"]) if tele else 0, "theme_hits": hits,
            "duration_s": float(d.get("duration_s") or clip_duration(clip) or 0.0)}


def _run_tool(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True,
                       env={**os.environ, "PYTHONUNBUFFERED": "1"})
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        raise RuntimeError(tail[-1][:200] if tail else f"exit {r.returncode}")
    return r.stdout


def _index_stage(j: journal.Journal, clip: str, stage: str) -> float:
    """Run one stage for one clip with the existing tools. Returns the cost in USD.
    Raises on failure; the caller records it and the journal decides on retries."""
    stem = Path(clip).stem
    src = clip_path(clip)
    if stage == "probe":
        if clip_duration(clip) is None:
            raise RuntimeError("ffprobe could not read the clip")
        j.set_captured(clip, capture_time(clip))
        return 0.0
    if stage == "telemetry":
        # Optional by Karl's rule: a clip without a gpmd stream skips, never fails.
        if not has_telemetry(clip):
            raise _Skip("no telemetry stream in the file")
        _run_tool(telemetry_cmd(stem))
        d = load_telemetry(clip)
        if not d:
            raise RuntimeError("the telemetry tool wrote no summary")
        if d.get("error") or not d.get("gpmd"):
            raise _Skip(str(d.get("error") or "no usable gpmd stream")[:120])
        return 0.0
    if stage == "asr":
        # One tool run over every clip still waiting on it, not one per clip: the
        # audio pass loads a 3 GB model per run, and it walks the folder anyway.
        wanted = [c for c in footage_clips()
                  if c == clip or (j.state(c, "asr") in journal.RETRYABLE
                                   and j.state(c, "probe") in journal.SETTLED)]
        skip = [Path(c).stem for c in footage_clips() if c not in wanted]
        for other in wanted:
            if other != clip:
                j.start(other, "asr")
        try:
            _run_tool(analyze_cmd(skip, False))
        except RuntimeError as exc:
            scope = procscope.current()
            for other in wanted:
                if other != clip:
                    if scope is not None and scope.cancelled:
                        j.requeue(other, "asr")      # stopped, not failed
                    else:
                        j.fail(other, "asr", str(exc))
            raise
        for other in wanted:
            if other == clip:
                continue
            if Path(other).stem in analysed_stems():
                j.set_facts(other, index_facts(other))
                j.finish(other, "asr")
            else:
                j.fail(other, "asr", "no sidecar written")
        if stem not in analysed_stems():
            raise RuntimeError("no sidecar written")
        j.set_facts(clip, index_facts(clip))
        return 0.0
    if stage == "proxy":
        build_proxy(src, STATE["proxy_dir"] / f"{stem}.mp4", STATE["orient"])
        measure_colour(clip, force=True)      # the proxy is the colour file's source
        STATE["proxies_ready"] = all(
            (STATE["proxy_dir"] / f"{Path(c).stem}.mp4").exists() for c in footage_clips())
        return 0.0
    if stage == "look":
        _run_tool(visual_cmd([stem], False))
        if not (STATE["visual"] / f"{stem}.visual.json").exists():
            raise RuntimeError("the visual pass wrote no sidecar")
        return sheets_for(clip) * VISUAL_USD_PER_SHEET
    if stage == "close":
        windows = STATE["work"] / f"windows_{stem}.json"
        try:
            _run_tool(scan_cmd([stem], windows, FINE_WINDOWS_PER_CLIP))
            found = json.loads(windows.read_text(encoding="utf-8")) if windows.exists() else {}
            spans = found.get(stem) if isinstance(found, dict) else found
            if not spans:
                raise _Skip("nothing worth a closer look")
            _run_tool(fine_cmd(windows))
        finally:
            windows.unlink(missing_ok=True)
        if not (STATE["visual"] / f"{stem}.fine.json").exists():
            raise RuntimeError("the close look wrote no sidecar")
        return len(spans) * FINE_USD_PER_WINDOW
    if stage == "picks":
        rebuild_events()
        return 0.0
    raise RuntimeError(f"no runner for stage {stage!r}")


class _Skip(Exception):
    """A stage that does not apply to this clip — no telemetry stream, nothing worth a
    close look. The journal records it as `skipped`, which counts as done."""


def _over_budget(next_usd: float) -> bool:
    cap = budget_cap()
    return cap is not None and project_spent() + next_usd > cap


def cap_words() -> str:
    """"this project's budget cap of $5.00 ($4.80 spent)" — what a refusal says; only
    asked when a cap is set."""
    return (f"this project's budget cap of ${budget_cap() or 0.0:.2f} "
            f"(${project_spent():.2f} spent)")


def refuse_past_cap(what: str, usd: float) -> None:
    """A call whose price is on its button is checked at that price before it starts,
    as the index, the audit and the deep look already were (M16 review): the gate every
    model call passes sees only a $0.05 pre-flight, so with a $5 cap and $4.90 spent a
    whole-cut Ask (~$0.58) started and landed the project near $5.48."""
    if _over_budget(usd):
        raise HTTPException(409, f"{what} (~${usd:.2f}) would pass {cap_words()} — "
                                 "raise or remove the cap in settings")


# ------------------------------------------------------------------ spend
# What this project has spent (INTAKE M16 I16.0g): one number per bin, read from disk,
# so it survives a restart and a bin switch — the process's own counter
# (`inference.spent_usd`) did neither, and capping it let a restart reset the cap and
# let one bin's spend pause another. Two sources, and no call is in both:
#   * the visual pass's sidecars. It runs as a subprocess, outside this process's
#     ledger, and its `.visual.json` / `.fine.json` carry what every look and close
#     look on this bin cost (the index's and the audit's — the fine file is a running
#     sum of every window paid for);
#   * `spend/<bin>.jsonl`, one row per model call made in this process — asks, effects,
#     themes, deep looks, finds, the estimates that draw a bar — written by the
#     listener below. Its first row is the history: what the records on disk (asks,
#     the themes proposal, deep looks) said this bin had spent before the file existed.
#     Until it exists those records are the number.

SPEND_DIR = "spend"
SPEND_LOCK = threading.Lock()
_USD_CACHE: dict[str, tuple[int, int, float]] = {}       # path → (mtime_ns, size, usd)


def spend_path() -> Path:
    return STATE["work"] / SPEND_DIR / f"{STATE['footage'].name}.jsonl"


def _usd_at(path: Path, *keys: str) -> float:
    """The number at `keys` in one JSON record, 0 when absent or unreadable; cached by
    mtime and size, because the status line asks every few seconds."""
    try:
        st = path.stat()
    except OSError:
        return 0.0
    hit = _USD_CACHE.get(str(path))
    if hit and hit[:2] == (st.st_mtime_ns, st.st_size):
        return hit[2]
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
        for k in keys:
            v = v.get(k) if isinstance(v, dict) else None
        usd = float(v or 0.0)
    except (OSError, ValueError, TypeError):
        usd = 0.0
    usd = usd if math.isfinite(usd) and usd > 0 else 0.0
    _USD_CACHE[str(path)] = (st.st_mtime_ns, st.st_size, usd)
    return usd


def _spend_rows_usd(path: Path) -> float | None:
    """The sum of the spend file's rows; None when there is no file yet."""
    try:
        st = path.stat()
    except OSError:
        return None
    hit = _USD_CACHE.get(str(path))
    if hit and hit[:2] == (st.st_mtime_ns, st.st_size):
        return hit[2]
    total = 0.0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                total += float(json.loads(line).get("usd") or 0.0)
            except (ValueError, TypeError, AttributeError):
                continue                 # a torn last line costs that row, not the total
    except OSError:
        return None
    _USD_CACHE[str(path)] = (st.st_mtime_ns, st.st_size, total)
    return total


def _visual_records(pattern: str) -> list[Path]:
    d = STATE.get("visual")
    return sorted(d.glob(pattern)) if d is not None and d.is_dir() else []


def _sidecar_usd() -> float:
    return sum(_usd_at(p, "projected_usd")
               for pat in ("*.visual.json", "*.fine.json") for p in _visual_records(pat))


def _history_usd() -> float:
    """In-process spend this bin's records carry: asks, the themes proposal, deep looks."""
    asks = STATE.get("asks")
    total = sum(_usd_at(p, "plan", "usage", "projected_usd")
                for p in (sorted(asks.glob("*.json")) if asks and asks.is_dir() else []))
    total += _usd_at(proposal_path(), "proposal", "usage", "projected_usd")
    total += sum(_usd_at(p, "projected_usd") for p in _visual_records("*.deep.json"))
    return total


def project_spent() -> float:
    """What this project (the bin open now) has spent, projected USD, from disk."""
    if not STATE.get("work") or not STATE.get("footage"):
        return 0.0
    rows = _spend_rows_usd(spend_path())
    return round(_sidecar_usd() + (_history_usd() if rows is None else rows), 4)


def _record_spend(result: inference.Result, role: str) -> None:
    """The listener: one row per call made in this process, on the bin open now — a
    job outlives nothing here, because the board will not switch bins while one runs."""
    if not STATE.get("work") or not STATE.get("footage"):
        return
    with SPEND_LOCK:
        path = spend_path()
        rows = []
        if not path.exists():
            rows.append({"ts": round(time.time(), 3), "kind": "before",
                         "usd": round(_history_usd(), 6),
                         "why": "asks, themes and deep looks on disk before this file"})
        rows.append({"ts": round(time.time(), 3), "role": role, "model": result.model,
                     "usd": result.projected_usd})
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))


def _project_gate(estimate: float) -> None:
    """Every model call this process makes asks first: with a cap set, a call that
    would take this project past it is refused, said in the cap's words — all but the
    backend probe's, on the thread it runs on (`_probe_job`)."""
    if getattr(_GATE_OFF, "probe", False):
        return
    if _over_budget(estimate):
        raise inference.BudgetExceeded(
            f"~${estimate:.2f} more would pass {cap_words()} — raise or remove the cap "
            f"in settings")


inference.add_spend_listener(_record_spend)
inference.set_budget_gate(_project_gate)


# The index's children, per job: the first pass's Stop kills them (`procscope`), the
# way the export's cancel kills its render — not after the clip-stage in hand.
INDEX_SCOPES: dict[str, procscope.Scope] = {}
INDEX_WAIT_S = 0.25


def _index_job(job: str, order: str) -> None:
    """Walk the journal until nothing is left to run. Stages run concurrently, each
    pool up to its worker count (`journal.DEFAULT_WORKERS`: one proxy encode beside two
    contact-sheet reads beside the audio pass), every tool inside the job's process
    scope. `entry["coarse"]` (the first pass sets it) asks the journal for the coarse
    tier first; `entry["coarse_ready"]` says when every clip is heard and looked at."""
    # background: its proxy encodes and close looks yield to playback (lane YIELD)
    scope = INDEX_SCOPES.setdefault(job, procscope.Scope(f"index {job}", background=True))
    kick_previews()          # a walk a cancel ended starts again with the index
    try:
        with procscope.active(scope):
            _index_walk(job, order, scope)
    finally:
        INDEX_SCOPES.pop(job, None)


def _index_take_in(j: journal.Journal, entry: progress.Job) -> list[str]:
    """Footage imported while this index runs: into the journal and probed, so the walk
    hears and previews it in this run (the walk read the clip list when it began)."""
    more = [c for c in footage_clips() if c not in j.clips or j.clips[c].get("missing")]
    if not more:
        return []
    j.add_clips(more, captured={c: capture_time(c) for c in more})
    for clip in more:
        if j.state(clip, "probe") in journal.RETRYABLE:
            j.start(clip, "probe")
            try:
                _index_stage(j, clip, "probe")
            except Exception as exc:  # noqa: BLE001
                j.fail(clip, "probe", str(exc)[:200])
            else:
                j.finish(clip, "probe")
    entry.note(f"added {len(more)} imported clip{'s' if len(more) != 1 else ''}")
    return more


def _index_walk(job: str, order: str, scope: procscope.Scope) -> None:
    entry = INDEXES[job]
    j = load_journal()
    workers = (load_settings().get("workers") or {})
    if workers:
        j.set_workers(workers)
    present = footage_clips()
    j.add_clips(present, captured={c: capture_time(c) for c in present})
    gone = [c for c in j.clips if c not in present]
    if gone:
        j.remove_missing(gone)
    if order in journal.ORDERS:
        j.set_order(order)
    changes = j.reconcile(index_files())
    if changes["requeued"] or changes["lost"]:
        entry.note("resumed — " + ", ".join(
            f"{Path(c).stem} {s}" for c, s in changes["requeued"] + changes["lost"]))
    for clip in present:
        if j.state(clip, "asr") == "done" and j.priority(clip) is None:
            j.set_facts(clip, index_facts(clip))
        # A telemetry skip recorded before the stage existed ("not integrated") is not
        # a fact about the clip. Reopen it where the file has a gpmd stream; the tool's
        # own cache makes the re-run free.
        st = j.clips[clip]["stages"]["telemetry"]
        if (st["state"] == "skipped"
                and str(st.get("last_error") or "").startswith("not integrated")
                and has_telemetry(clip)):
            j.reopen(clip, "telemetry")
    # Probes first, all of them: they are one ffprobe each, and with every probe settled
    # the audio pass can run as one batch over every clip instead of reloading its
    # model per clip as the priority walk would otherwise make it.
    for clip in present:
        if j.state(clip, "probe") in journal.RETRYABLE:
            j.start(clip, "probe")
            try:
                _index_stage(j, clip, "probe")
            except Exception as exc:  # noqa: BLE001
                j.fail(clip, "probe", str(exc)[:200])
            else:
                j.finish(clip, "probe")
    entry["state"] = "running"

    def report(detail: str) -> None:
        prog = j.progress()
        entry["journal"] = prog
        entry["released"] = prog.get("released", 0)
        entry["cost_usd"] = prog.get("cost_usd", 0.0)
        entry["coarse_ready"] = j.coarse_ready()
        entry.advance("index", max(0.0, min(1.0, float(prog.get("pct") or 0.0) / 100.0)),
                      detail=detail)

    cond = threading.Condition()
    landed: list[tuple[str, str, str, object]] = []
    running: dict[tuple[str, str], threading.Thread] = {}

    def work(clip: str, stage: str) -> None:
        with procscope.active(scope):
            try:
                out: tuple[str, object] = ("done", _index_stage(j, clip, stage))
            except _Skip as why:
                out = ("skip", str(why))
            except Exception as exc:  # noqa: BLE001 — a failed stage is a state, not a stop
                out = ("fail", exc)
        with cond:
            landed.append((clip, stage, *out))
            cond.notify_all()

    def settle(clip: str, stage: str, kind: str, val) -> None:
        stem = Path(clip).stem
        if kind != "done" and (entry.get("cancel") or scope.cancelled):
            j.requeue(clip, stage)               # killed by the Stop: not a failure
            return
        if kind == "skip":
            j.skip(clip, stage, str(val))
        elif kind == "fail":
            state = j.fail(clip, stage, str(val)[:200],
                           retry_after_s=30.0 if "rate" in str(val).lower() else None)
            report(f"{stem} · {stage} failed ({state}): {str(val)[:80]}")
            return
        else:
            j.finish(clip, stage, float(val or 0.0))
            # The priority facts read the telemetry summary; on a bin whose sidecars
            # already existed they were computed at reconcile time, before this stage
            # ran, so they are refreshed once the sensor has spoken.
            if stage == "telemetry" and j.state(clip, "asr") in journal.SETTLED:
                j.set_facts(clip, index_facts(clip))
        report(f"{stem} released" if j.released(clip) else f"{stem} · {stage} done")

    report("reading the journal")
    while True:
        with cond:
            got, landed[:] = list(landed), []
        for clip, stage, kind, val in got:
            running.pop((clip, stage), None)
            settle(clip, stage, kind, val)
        if entry.get("cancel") or scope.cancelled:   # the Stop: wait for the killed tools
            if not running:
                break
        else:
            # The bin's junk word, before every pick: a clip the editor confirmed while
            # the last stage ran never gets its sheets bought, and a clip whose proxy
            # just landed is measured and, if it looks like junk, waits behind the clean
            # ones.
            try:
                sync_junk(j)
            except (OSError, ValueError, journal.JournalError) as exc:
                entry.note(f"junk check skipped: {str(exc)[:80]}")
            coarse = bool(entry.get("coarse"))
            # Footage imported while the index runs (wave 12 IMPORT) joins this run:
            # added to the journal, probed, then walked like the rest.
            asked = int(entry.get("asked") or 0)
            if asked > int(entry.get("took") or 0):
                _index_take_in(j, entry)
                entry["coarse_ready"] = j.coarse_ready()
                entry["took"] = asked
            # The cut's clips take the proxy pool first, in film order (verdict 5 punch 2:
            # the Program shows a 360p preview until a clip's 720p proxy lands) — once the
            # first pass's coarse tier, what its proposal waits on, is through.
            pick = _cut_proxy_next(j) if not coarse or j.coarse_ready() else None
            if pick is not None:
                try:
                    j.start(pick, "proxy", coarse=coarse)
                except journal.JournalError:
                    pick = None
            if pick is not None:
                report(f"{Path(pick).stem} · proxy (in the cut)")
                th = threading.Thread(target=work, args=(pick, "proxy"), daemon=True)
                running[(pick, "proxy")] = th
                th.start()
            while True:
                # a free run (started by an import) holds the priced stages for this
                # run only: the sheet pool at 0, never the journal's own pause
                nxt = j.next(coarse=coarse, workers=FREE_ONLY if entry.get("free") else None)
                if nxt is None:
                    break
                clip, stage = nxt
                stem = Path(clip).stem
                if stage in journal.PRICED:
                    est = (VISUAL_USD_PER_SHEET if stage == "look"
                           else FINE_WINDOWS_PER_CLIP * FINE_USD_PER_WINDOW)
                    if _over_budget(est):
                        j.pause_priced(f"budget cap ${budget_cap() or 0.0:.2f} reached — "
                                       f"${project_spent():.2f} spent on this project")
                        report(f"budget cap reached — priced stages paused; {stem} waits")
                        continue
                j.start(clip, stage, coarse=coarse)
                report(f"{stem} · {stage}")
                th = threading.Thread(target=work, args=(clip, stage), daemon=True)
                running[(clip, stage)] = th
                th.start()
            if not running:
                if int(entry.get("asked") or 0) > int(entry.get("took") or 0):
                    continue                 # new footage asked in meanwhile: take it
                break
        with cond:
            if not landed:
                cond.wait(timeout=INDEX_WAIT_S)
    prog = j.progress()
    entry["journal"] = prog
    entry["coarse_ready"] = j.coarse_ready()
    if entry.get("cancel") or scope.cancelled:
        entry.finish("cancelled", detail="stopped — what finished is kept; index the rest any time")
        return
    entry.complete("index")
    n = len([c for c in j.clips if not j.clips[c].get("missing")])
    tail = (f" — priced stages paused: {j.data.get('paused_reason') or 'budget cap'}"
            if j.paused_priced else "")
    if entry.get("free") and not tail:
        held = sum(1 for c in j.clips if not j.clips[c].get("missing")
                   and j.state(c, "look") in journal.RETRYABLE)
        if held:
            tail = (f" — {held} clip{'s' if held != 1 else ''} not looked at yet: the look "
                    "is priced, so it waits for you (Run first pass)")
    parked = prog.get("parked", 0)
    if parked:
        tail += f" — {parked} clip{'s' if parked != 1 else ''} parked"
    entry.finish("done", detail=f"{prog.get('released', 0)} of {n} clips released{tail}")


SESSION_GAP_S = 4 * 3600      # revise.shot_timeline's rule: a 4 h gap starts a new session


def has_telemetry(clip: str) -> bool | None:
    """Whether the file carries a GoPro `gpmd` stream (R11). One ffprobe, cached; None
    when the footage is missing. Presence is a fact shown to the human, never assumed —
    Karl's rule: the pipeline runs identically without it."""
    cache: dict = STATE.setdefault("telemetry", {})
    if clip in cache:
        return cache[clip]
    src: Path = clip_path(clip)
    if not src.exists():
        return None
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "d", "-show_entries",
         "stream=codec_tag_string", "-of", "csv=p=0", str(src)],
        capture_output=True, text=True)
    cache[clip] = "gpmd" in (r.stdout or "")
    return cache[clip]


# Telemetry, on R11's terms (research/R11-telemetry.md, INTAKE M6): a freefall run under
# 0.5 g for at least 0.25 s is clean evidence of motion and may corroborate; an impact peak
# is a number on the witness and nothing more, and only above 5 g is it worth showing —
# at 3 g the rule fires six times a minute on this footage, mostly hands on the camera.
FREEFALL_MIN_S = 0.25
IMPACT_SHOW_G = 5.0


def telemetry_dir() -> Path:
    return STATE["work"] / "telemetry" / STATE["footage"].name


def telemetry_cmd(stem: str) -> list[str]:
    """R11's tool over one clip — a function so the tests can replace it. Free: ffmpeg
    and pure Python, no model call."""
    return ["uv", "run", "--quiet", str(TOOLS / "telemetry.py"), str(tool_footage()),
            "-o", str(telemetry_dir()), "--only", stem]


def load_telemetry(clip: str) -> dict | None:
    p = telemetry_dir() / f"{Path(clip).stem.upper()}.telemetry.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def telemetry_peaks(clip: str) -> dict | None:
    """The `felt` witnesses for a clip — numbers only: loose freefall runs of at least
    FREEFALL_MIN_S, and impact peaks above IMPACT_SHOW_G. None when the clip has no
    telemetry summary, so a phone clip contributes nothing and loses nothing."""
    d = load_telemetry(clip)
    if not d or not d.get("gpmd"):
        return None
    peaks = []
    for run in d.get("freefall_loose") or []:
        try:
            if float(run["dur"]) >= FREEFALL_MIN_S:
                peaks.append({"at": round(float(run["start"]), 2),
                              "value": round(float(run["dur"]), 2),
                              "unit": f"s freefall at {float(run.get('min_g') or 0):.2f} g",
                              "kind": "freefall"})
        except (KeyError, TypeError, ValueError):
            continue
    for p in d.get("impacts") or []:
        try:
            if float(p["value"]) >= IMPACT_SHOW_G:
                peaks.append({"at": round(float(p["at"]), 2),
                              "value": round(float(p["value"]), 1), "unit": "g",
                              "kind": "impact"})
        except (KeyError, TypeError, ValueError):
            continue
    peaks.sort(key=lambda x: x["at"])
    return {"peaks": peaks, "orientation": d.get("orientation")}


def bin_telemetry() -> dict[str, dict]:
    out = {}
    for clip in footage_clips():
        t = telemetry_peaks(clip)
        if t:
            out[clip] = t
    return out


# Cold land (redesign wave 2, INGEST): /api/clips is the Project panel's first paint, so
# it never waits on a pass and never runs its probes one after another. Each clip's
# free facts (length, capture time, telemetry, frame rate, size, codec) are probed once
# on a small pool, started at configure(); a request waits at most CLIP_FACTS_WAIT_S for
# the ones still running and answers with the rest as null and `probing` > 0, and the
# board asks again a second later.
CLIP_FACTS_WAIT_S = 1.0
CLIP_FACTS_WORKERS = 4
CLIP_FACTS: dict = {"lock": threading.Lock(), "pool": None, "running": {}}


_SOURCE_TC = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})([:;.,])(\d{2,3})$")


def source_tc(tag: str | None, fps: float | None) -> str | None:
    """A camera's timecode tag as the Project panel's Media Start writes it: HH:MM:SS:FF
    at the clip's own rate, HH:MM:SS;FF when it counts drop-frame (29.97 / 59.94, the
    tag's `;` or `,`); None when the file has none, or one that is not a timecode at that
    rate (a frame field past the rate's last frame)."""
    m = _SOURCE_TC.match(str(tag or "").strip())
    if not m:
        return None
    hh, mm, ss, sep, ff = m.groups()
    nominal = max(1, round(float(fps))) if fps else 0
    if int(mm) > 59 or int(ss) > 59 or (nominal and int(ff) >= nominal):
        return None
    drop = sep in ";," and nominal in (30, 60)
    return f"{int(hh):02d}:{mm}:{ss}{';' if drop else ':'}{int(ff):02d}"


def _probe_clip_facts(clip: str, footage: Path) -> dict | None:
    """Every free fact of one clip, with as few ffprobes as the caches allow: one for
    length + creation time + data streams (seeding clip_duration / capture_time /
    has_telemetry's caches), then MEDIA's cached `media_facts` for the picture."""
    if STATE.get("footage") != footage:
        return None                      # re-pointed at another bin meanwhile
    src = clip_path(clip)
    if not src.is_file():
        return None
    durations = STATE.setdefault("durations", {})
    capture = STATE.setdefault("capture", {})
    tele = STATE.setdefault("telemetry", {})
    if clip not in durations or clip not in capture or clip not in tele:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:format_tags=creation_time:stream=codec_type,codec_tag_string",
             "-of", "json", str(src)], capture_output=True, text=True)
        try:
            info = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
        except ValueError:
            info = {}
        fmt = info.get("format") or {}
        if STATE.get("footage") != footage:
            return None
        if clip not in durations:
            d = load_sidecar(clip).get("duration_s")
            if d is None:
                try:
                    d = round(float(fmt.get("duration")), 2)
                except (TypeError, ValueError):
                    d = None
            durations[clip] = d
        if clip not in capture:
            when = None
            raw = str((fmt.get("tags") or {}).get("creation_time") or "")
            if raw:
                try:
                    when = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    when = None
            capture[clip] = when if when is not None else src.stat().st_mtime
        if clip not in tele and info:
            tele[clip] = any(s.get("codec_tag_string") == "gpmd"
                             for s in info.get("streams") or [])
    f = media_facts(src) or {}
    facts = {"fps": round(f["fps"], 3) if f.get("fps") else None,
             "width": f.get("width") or None, "height": f.get("height") or None,
             "codec": f.get("vcodec"),
             "tc_start": source_tc(f.get("timecode"), f.get("fps")),
             "audio": ({"channels": f.get("channels"), "rate": f.get("sample_rate")}
                       if f.get("acodec") and (f.get("channels") or f.get("sample_rate"))
                       else None)}
    if STATE.get("footage") == footage:
        STATE.setdefault("clip_facts", {})[clip] = facts
    return facts


def warm_clip_facts(clips: list[str]) -> list:
    """Start probing every clip whose facts are not known yet; the futures still
    running. Called at configure() (the boot path) and by each /api/clips."""
    from concurrent.futures import ThreadPoolExecutor
    known = STATE.setdefault("clip_facts", {})
    footage = STATE["footage"]
    out = []
    with CLIP_FACTS["lock"]:
        if CLIP_FACTS["pool"] is None:
            CLIP_FACTS["pool"] = ThreadPoolExecutor(CLIP_FACTS_WORKERS,
                                                    thread_name_prefix="clipfacts")
        running = CLIP_FACTS["running"]
        fresh = []
        for clip in clips:
            if clip in known:
                continue
            key = (str(footage), clip)
            fut = running.get(key)
            if fut is None or (fut.done() and clip not in known):
                fut = running[key] = CLIP_FACTS["pool"].submit(_probe_clip_facts, clip, footage)
                fresh.append(clip)
            if not fut.done():
                out.append(fut)
        # then the colour tags /api/colour reads (one ffprobe a clip, sequential there):
        # queued behind every clip's facts, so the Project panel is never kept waiting
        for clip in fresh:
            CLIP_FACTS["pool"].submit(_warm_colour_probe, clip, footage)
    return out


def _warm_colour_probe(clip: str, footage: Path) -> None:
    if STATE.get("footage") == footage:
        clip_probe(clip)


# A clip's card picture (the Project panel's and /open's): 1 s in, not the first frame,
# which is black on many camera clips (Killington CLIP_12 is black for its first three
# frames). No decode to find it: a fixed offset the background walk pre-builds.
POSTER_AT_S = 1.0


def poster_time(clip: str) -> float:
    """Where a clip's card frame is taken: POSTER_AT_S, or half a shorter clip; 0 while
    its length is not known yet (cached lengths only — this never probes)."""
    d = STATE.get("durations", {}).get(clip)
    if not d:
        return 0.0
    return round(min(POSTER_AT_S, float(d) / 2), 2)


def unheard_clip(clip: str, used: bool = False) -> dict:
    """A footage clip as `project_payload` lists one, before its sound is heard: the
    free facts, no transcript, no candidates. `heard: False` says which."""
    stem = Path(clip).stem
    return {
        "clip": clip, "stem": stem, "duration": on_clip_grid(clip_duration(clip), clip_rate(clip)),
        "fps": clip_rate(clip),
        "proxy": f"/media/proxy/{stem}.mp4", "poster": poster_url(stem),
        "transcript": [], "visual": load_visual(clip), "captured": capture_time(clip),
        "candidates": [],
        "summary": {"speech_fraction": None, "wind_dominant_fraction": None,
                    "integrated_lufs": None, "audio_usable": None},
        "used": used, "heard": False,
    }


@app.get("/api/clips")
def api_clips() -> JSONResponse:
    """The folder as a contact sheet would show it (design §2): every clip with the
    free facts — length, when it was shot and which session that makes it, what exists
    for it on disk, whether it carries telemetry, its frame rate, size and codec, its
    source timecode and its sound's channels and rate — and
    the journal's word on it when the bin has been indexed. Nothing here costs a model
    call, and nothing waits on the index: a clip still being probed after
    CLIP_FACTS_WAIT_S comes back with null facts (`probing` counts them)."""
    from concurrent.futures import wait as _wait
    clips = footage_clips()
    pending = warm_clip_facts(clips)
    if pending:
        _wait(pending, timeout=CLIP_FACTS_WAIT_S)
    known = STATE.setdefault("clip_facts", {})
    ready = {c for c in clips if c in known}
    stamped = sorted(((capture_time(c) or 0.0), c) for c in clips if c in ready)
    session, prev, session_of = 0, None, {}
    for when, clip in stamped:
        if prev is None or when - prev > SESSION_GAP_S:
            session += 1
        session_of[clip] = session
        prev = when
    j = None
    if journal_path().exists():
        try:
            j = load_journal()
        except journal.JournalError:
            j = None
    released = set(released_clips())
    # The junk band I5.1 wanted on the wire: the proposal's one word per clip.
    junk_of = {r["clip"]: r["state"] for r in junk_rows()}
    heard, looked, closed = analysed_stems(), visual_stems(), fine_stems()
    sources = media_sources()
    out = []
    for clip in clips:
        stem = Path(clip).stem
        rec = j.clips.get(clip) if j else None
        ok = clip in ready
        f = known.get(clip) or {}
        out.append({
            "clip": clip, "stem": stem,
            "duration": on_clip_grid(clip_duration(clip), f.get("fps")) if ok else None,
            "captured": capture_time(clip) if ok else None,
            "session": session_of.get(clip),
            "proxy": (STATE["proxy_dir"] / f"{stem}.mp4").exists(),
            "poster": poster_url(stem, poster_time(clip) if ok else 0.0),
            "analysed": stem in heard,
            "looked": stem in looked,
            "closed": stem in closed,
            "telemetry": has_telemetry(clip) if ok else None,
            "fps": f.get("fps"), "width": f.get("width"), "height": f.get("height"),
            "codec": f.get("codec"),
            # Media Start's source timecode (the camera's, HH:MM:SS:FF at the clip's rate;
            # null without one) and Audio Info's channels + sample rate (null: no sound)
            "tc_start": f.get("tc_start"), "audio": f.get("audio"),
            "released": clip in released,
            "junk": junk_of.get(clip, "clean"),
            # where it lives (wave 12 IMPORT): the bin's folder, imported in place, or
            # uploaded from the viewer's computer — Premiere's Media Path
            **_clip_origin(clip, sources),
            "media": media_state(clip),       # the bin's "preview building" (verdict 8)
            "journal": None if rec is None else {
                "priority": rec.get("priority"), "missing": bool(rec.get("missing")),
                "parked": rec.get("parked"),
                "stages": {s: rec["stages"][s]["state"] for s in journal.STAGES}},
        })
    return JSONResponse({"clips": out, "sessions": session,
                         "footage": str(STATE["footage"]),
                         "total_s": round(sum(c["duration"] or 0.0 for c in out), 2),
                         "journal": journal_path().exists(),
                         "paused_priced": bool(j and j.paused_priced),
                         "probing": len(clips) - len(ready)})


@app.post("/api/index")
async def api_index(request: Request) -> JSONResponse:
    """Start — or resume — the unattended index. One at a time; the journal on disk is
    the state, so a second call after a crash is a resume and a call after new footage
    is dropped into the folder is "index what isn't done"."""
    body = await request.json() if int(request.headers.get("content-length") or 0) else {}
    live = next((x for x in INDEXES.values() if x["state"] not in progress.TERMINAL), None)
    if live is not None and live.get("free") and not live.get("cancel") \
            and not body.get("pause_priced"):
        # the free run an import started: asking for the index is asking for its priced
        # stages too, so that run takes them on (no second index, no 409)
        live["free"] = False
        live["label"] = "Indexing the footage"
        if body.get("resume_priced"):
            load_journal().resume_priced()
        return JSONResponse({"job": live["id"], "joined": True})
    if live is not None:
        raise HTTPException(409, "the index is already running")
    if not footage_clips():
        raise HTTPException(400, "no footage to index")
    order = str(body.get("order") or "")
    if order and order not in journal.ORDERS:
        raise HTTPException(400, f"order must be one of {journal.ORDERS}")
    if body.get("interval_s") is not None:
        try:
            interval = float(body["interval_s"])
        except (TypeError, ValueError):
            raise HTTPException(400, "interval_s must be a number")
        if interval not in VISUAL_INTERVALS:
            raise HTTPException(400, f"interval_s must be one of {list(VISUAL_INTERVALS)}")
        set_look_interval(interval)
    # The human's say on the priced stages, since the cap pauses them on its own:
    # `resume_priced` lifts the pause (the cap is checked again before each priced
    # stage), `pause_priced` holds them for this run.
    if body.get("resume_priced"):
        load_journal().resume_priced()
    elif body.get("pause_priced"):
        load_journal().pause_priced("paused by the editor")
    job = index_entry()
    threading.Thread(target=_index_run, args=(job, order), daemon=True).start()
    return JSONResponse({"job": job})


def _index_run(job: str, order: str) -> None:
    """`_index_job` on its own thread: whatever happens, the job ends."""
    try:
        _index_job(job, order)
    except Exception as exc:  # noqa: BLE001 — the index job must end, whatever happened
        if INDEXES[job]["state"] not in progress.TERMINAL:
            INDEXES[job].finish("failed", detail=f"{type(exc).__name__}: {exc}"[:300])


def index_entry(free: bool = False) -> str:
    """A new index job in INDEXES, the way /api/index and the first pass start one."""
    job = uuid.uuid4().hex[:8]
    INDEXES[job] = progress.Job(
        "index", "Indexing the new footage (free)" if free else "Indexing the footage",
        id=job, state="running", journal=None, free=free,
        released=0, cost_usd=0.0, detail="opening the journal")
    # No clock estimate: a fixed 60 s called "measured" drew 18 % at 7 s and 90 % for
    # 12 min with nothing heard (verdict 2). The bar is held to the journal's counted
    # clip-stages and the time left is the journal's measured one, or none.
    INDEXES[job].set_estimate(
        None, [progress.milestone("index", "indexing, clip by clip", 1.0)],
        source="none")
    flowmod.honest_index_job(INDEXES[job])
    return job


@app.get("/api/index")
def api_index_status() -> JSONResponse:
    """Where the index is: the journal's own progress (per-clip rows, counts, cost,
    released), whether a run is going, and the last run's id."""
    path = journal_path()
    running = next((x for x in INDEXES.values()
                    if x["state"] not in progress.TERMINAL), None)
    if not path.exists():
        return JSONResponse({"exists": False, "running": running is not None,
                             "interval_s": look_interval(),
                             "job": running["id"] if running else None})
    j = load_journal()
    return JSONResponse({"exists": True, "path": str(path), "order": j.order,
                         "paused_priced": j.paused_priced,
                         # what the pause holds, priced — the paused box says it
                         "waiting": journal_waiting(j),
                         "interval_s": look_interval(),
                         "progress": j.progress(), "released": j.released_clips(),
                         "running": running is not None,
                         "job": running["id"] if running else None})


INDEX_CANCEL_WAIT_S = 5.0


@app.post("/api/index/cancel")
def api_index_cancel() -> JSONResponse:
    """Stop the background index — its proxies, close looks, contact-sheet reads, the
    audio pass — and every preview and poster encode, now (verdict 4, punch 8: after the
    first pass moved on, the index kept encoding with nothing on screen able to stop it).
    The tools die with their process groups (`procscope`), the way the first pass's Stop
    and the export's cancel kill theirs. What finished is kept; a stage that was killed
    goes back in the queue (`journal.requeue`), so no clip reads as heard, looked at or
    proxied when it is not, and a killed preview is "not built", never "failed". A first
    pass still waiting on the index stops with it. Returns once the index has ended (or
    after INDEX_CANCEL_WAIT_S), so the job pill is gone on the next poll."""
    fp = _fp_live()
    stopped_pass = None
    if fp is not None and (fp.get("stage") or "index") == "index":
        try:
            stopped_pass = json.loads(api_firstpass_cancel().body).get("job")
        except HTTPException:
            stopped_pass = None
    live = [x for x in INDEXES.values() if x["state"] not in progress.TERMINAL]
    for x in live:
        x["cancel"] = True
        x.note("stopping — what finished is kept")
    scopes = [INDEX_SCOPES[x["id"]] for x in live if x["id"] in INDEX_SCOPES]
    killed: list[int] = []

    def kill(sc: procscope.Scope) -> None:
        killed.append(sc.kill())

    killers = [threading.Thread(target=kill, args=(sc,), daemon=True) for sc in scopes]
    killers.append(threading.Thread(target=lambda: killed.append(stop_media()), daemon=True))
    for t in killers:
        t.start()
    for t in killers:
        t.join(timeout=procscope.GRACE_S + 1.0)
    deadline = time.time() + INDEX_CANCEL_WAIT_S
    while time.time() < deadline and any(x["state"] not in progress.TERMINAL for x in live):
        time.sleep(0.05)
    return JSONResponse({"stopped": [x["id"] for x in live],
                         "states": {x["id"]: x["state"] for x in live},
                         "first_pass": stopped_pass, "killed": sum(killed)})


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


# How often a render's progress is re-read. Faster than PROGRESS_TICK_S: the Export
# dialog's bar is read off ffmpeg's own `-progress` files, which is cheap, and a bar that
# moves twice a second reads as working where one that jumps every two reads as stuck.
RENDER_TICK_S = 0.4
RENDER_PROCS: dict[str, subprocess.Popen] = {}


def _part_progress(path: Path) -> tuple[float, bool]:
    """(seconds encoded, finished) from one ffmpeg `-progress` file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0.0, False
    out_s, ended = 0.0, False
    for line in text.splitlines():
        key, _, val = line.partition("=")
        if key in ("out_time_us", "out_time_ms"):      # both are microseconds
            try:
                out_s = max(out_s, int(val) / 1e6)
            except ValueError:
                pass
        elif key == "progress":
            ended = val.strip() == "end"
    return out_s, ended


def encoded_seconds(parts_dir: Path, durs: list[float]) -> float:
    """How many seconds of the film ffmpeg has encoded so far, against `durs` (each
    shot's length in the film). A finished part counts whole; the one being encoded
    counts its `out_time`, never more than its own length."""
    if not parts_dir.exists():
        return 0.0
    got = 0.0
    for i, d in enumerate(durs):
        out_s, ended = _part_progress(parts_dir / f"part_{i:03d}.progress")
        got += d if ended else min(out_s, d)
    return got


def _run_render(job: str, cmd: list[str]) -> subprocess.CompletedProcess:
    """Run assemble.py in its own process group, so a cancel takes uv, python and
    ffmpeg down together (`/api/render/<job>/cancel`)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    RENDER_PROCS[job] = proc
    try:
        if (RENDERS.get(job) or {}).get("cancelled"):   # cancelled before it had a process
            os.killpg(proc.pid, 15)
        out, err = proc.communicate()
    finally:
        RENDER_PROCS.pop(job, None)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


FAILED_SHOT_PREFIX = "failed shot: "     # assemble.say_failed_shot's line
LOG_TAIL_LINES = 40


def failed_shot(log: str) -> dict | None:
    """The shot whose part failed, from assemble.py's `failed shot: {json}` line (the
    first in film order when several parts failed at once), or None."""
    found = []
    for line in (log or "").splitlines():
        if line.startswith(FAILED_SHOT_PREFIX):
            try:
                d = json.loads(line[len(FAILED_SHOT_PREFIX):])
            except ValueError:
                continue
            if isinstance(d, dict) and isinstance(d.get("index"), int):
                found.append(d)
    return min(found, key=lambda d: d["index"]) if found else None


def log_tail(log: str, n: int = LOG_TAIL_LINES) -> str:
    """The render log's last `n` lines, for the Export dialog's Show log: Python's
    tracebacks (the frames, not the error they end in) left out — an editor reads what
    ffmpeg said, not which line of assemble.py raised it."""
    keep, in_tb = [], False
    for line in (log or "").rstrip().splitlines():
        if line.startswith("Traceback (most recent call last)") or line.startswith(
                "During handling of the above exception"):
            in_tb = True
            continue
        if in_tb and (not line.strip() or line[:1].isspace()):
            continue
        in_tb = False
        keep.append(line)
    return "\n".join(keep[-n:])


def failure_detail(shot: dict | None, log: str) -> str:
    """What the Export dialog says about a failed render (verdict 7, blocker 2: it said
    "the render failed — see the log", and the log was not reachable from it): the shot
    and its clip when a part failed, else ffmpeg's last word."""
    if shot:
        rng = ""
        if isinstance(shot.get("in"), (int, float)) and isinstance(shot.get("out"), (int, float)):
            rng = f" {float(shot['in']):.2f}–{float(shot['out']):.2f} s"
        reason = str(shot.get("reason") or "").strip()
        return (f"shot {shot['index'] + 1} ({shot.get('clip') or 'a clip'}{rng}) did not "
                f"render{': ' + reason if reason else ''}")
    last = [ln.strip() for ln in (log or "").splitlines() if ln.strip()]
    return f"the render failed: {last[-1][-200:]}" if last else "the render failed"


def _render_job(job: str, edl_path: Path, out_path: Path, meta: dict) -> None:
    entry = RENDERS[job]
    parts_dir = STATE["work"] / f"parts_{job}"
    export = bool(meta.get("export"))
    # An export is written under a hidden name and renamed once it is whole, so nothing
    # listing the folder (the versions list, a file manager) ever sees half a file.
    write_to = out_path.with_name(f".{out_path.stem}.{job}.partial.mp4") if export else out_path
    cmd = ["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
           "--footage", str(tool_footage()), "--sidecars", str(STATE["sidecars"]),
           "--assets", str(STATE["assets"]), "--profile", meta.get("profile", "preview"),
           "--colour-dir", str(colour_dir()),
           "--generated-dir", str(generated_dir()),
           "--parts-dir", str(parts_dir), "--progress", "-o", str(write_to)]
    if meta.get("fps"):
        cmd[-2:-2] = ["--fps", str(meta["fps"])]
    durs = [edits.dur(s) for s in meta.get("shots") or []]
    planned = sum(durs) or 1e-9

    # Same shape as the audio pass: a ticker reading the parts on disk, so the UI can
    # say "cutting 7/16" instead of "rendering…" for two minutes. Karl, on clicking
    # Render: "got like no response - and just see rendering..." Since the Export
    # dialog (verdict 2 punch 5) the cutting share is ffmpeg's own `-progress`: seconds
    # of film encoded against the sequence's length, not a count of files started.
    stop = threading.Event()

    def count() -> None:
        started = len(list(parts_dir.glob("part_*.mp4"))) if parts_dir.exists() else 0
        if parts_dir.exists() and any(parts_dir.glob("part_*.progress")):
            ends = [_part_progress(parts_dir / f"part_{i:03d}.progress")[1]
                    for i in range(len(durs))]
            finished = sum(ends)
            enc = encoded_seconds(parts_dir, durs)
        else:
            # No `-progress` beside the parts (an assemble.py that does not write
            # it): a part on disk is a part done, as it always was.
            finished = started
            enc = sum(durs[:started])
        entry["done"] = finished
        entry["encoded_s"] = round(enc, 2)
        joining = finished >= entry["total"]
        entry["stage"] = "joining" if joining else "cutting"
        if joining:
            # The old bar sat at 100% here, and joining a 4K delivery render is
            # minutes of it. A separate milestone means the bar stops at the share
            # cutting was worth and keeps counting the rest.
            entry.complete("cutting", detail=f"joining {entry['total']} shots")
        else:
            entry.advance("cutting", enc / planned,
                          detail=f"cutting shot {finished + 1} of {entry['total']}")
        # The Export dialog's bar: work done, from the milestones alone — never the
        # clock, so it cannot run ahead of the encoder.
        frac = entry.fraction() or 0.0
        entry["progress_pct"] = max(entry.get("progress_pct") or 0.0,
                                    round(min(99.0, 100.0 * frac), 1))

    def tick() -> None:
        while not stop.wait(min(RENDER_TICK_S, PROGRESS_TICK_S)):
            try:
                count()
            except OSError:
                pass

    ticker = threading.Thread(target=tick, daemon=True)
    ticker.start()
    try:
        r = _run_render(job, cmd)
        stdout, stderr, rc = r.stdout, r.stderr, r.returncode
    finally:
        stop.set()
        shutil.rmtree(parts_dir, ignore_errors=True)
    if entry.get("cancelled"):
        if export:
            write_to.unlink(missing_ok=True)
        else:
            out_path.unlink(missing_ok=True)
        entry.update(log=(stdout or "") + (stderr or ""), output=None, url=None)
        entry["stage"] = "cancelled"
        entry.finish("cancelled", detail="cancelled — nothing was written")
        return
    ok = rc == 0
    if ok:
        # Written next to the file rather than kept in memory: renders outlive the
        # process, and a versions list that empties on restart is not a versions list.
        #
        # And written *before* the job reports done, because the UI refreshes the
        # versions list the moment it sees "done" — a render that announces itself
        # before its own metadata exists gets listed as an unlabelled older file.
        meta["duration_s"] = probe_duration(write_to)
        res = probe_resolution(write_to)
        if res:
            meta["width"], meta["height"] = res
        out_path.with_suffix(".json").write_text(json.dumps(meta, indent=1),
                                                 encoding="utf-8")
        if export:
            write_to.replace(out_path)
    elif export:
        write_to.unlink(missing_ok=True)
    entry.update(
        done=entry["total"] if ok else entry["done"],
        log=(stdout or "") + (stderr or ""),
        output=str(out_path) if ok else None,
        url=media_url_for(out_path) if ok else None,
    )
    if not ok:
        entry["stage"] = "failed"
        shot = failed_shot(entry["log"])
        entry["failed_shot"] = shot
        entry["log_tail"] = log_tail(entry["log"])
        entry.finish("failed", detail=failure_detail(shot, entry["log"]))
        return

    if export:
        # An export is the file somebody asked for, at the name and place they gave;
        # the 720p copy the board's players stream is the board's business and is
        # made lazily, the first time the versions list sees the file.
        entry["stage"] = "done"
        entry["progress_pct"] = 100.0
        mb = out_path.stat().st_size / (1024 * 1024)
        frame = f"{meta['width']}×{meta['height']}" if meta.get("width") else ""
        entry.finish("done", detail=" · ".join(b for b in (
            f"{meta.get('duration_s') or 0:.2f} s", frame,
            f"{mb:.1f} MB" if mb < 10 else f"{mb:.0f} MB",
            meta.get("preset_label") or meta.get("profile")) if b))
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
    entry["progress_pct"] = 100.0
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


REVIEW_SCOPES: dict = {}


def review_scope() -> procscope.Scope:
    """The scope every review copy's ffmpeg runs in (verdict 7, punch 10: an export's
    review copy outlived the server). Its own session (process group) and PR_SET_PDEATHSIG
    as the YIELD encodes have, and the shutdown hook's `procscope.kill_all` ends it. Not
    background: a render's own copy is a phase somebody is watching, so it is neither
    niced nor paused. Fresh after a shutdown's kill."""
    with REVIEW_LOCK:
        sc = REVIEW_SCOPES.get("scope")
        if sc is None or sc.cancelled:
            sc = REVIEW_SCOPES["scope"] = procscope.Scope("review copies")
        return sc


def review_cmd(src: Path, tmp: Path) -> list[str]:
    """The ffmpeg that makes a review copy: 720p, faststart — the proxies' recipe."""
    return ["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", str(src),
            "-map", "0:v:0", "-map", "0:a:0?", "-dn",
            # Capped by width, never upscaled: a preview render is already small and
            # re-encoding it bigger would spend bytes on detail that is not there.
            "-vf", f"scale=min({PROXY_W}\\,iw):-2", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", str(PROXY_CRF),
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)]


def build_review(src: Path, dest: Path) -> None:
    """A 720p, faststart copy of a finished render — same recipe as the proxies —
    made in `review_scope`, so it dies with the server."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(f".{os.getpid()}.part.mp4")
    with procscope.active(review_scope()):
        r = subprocess.run(review_cmd(src, tmp), capture_output=True, text=True)
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
    # A copy's name too — two cuts of one bin downloaded with the same name is the
    # confusion the name exists to prevent. The bin's first cut stays as it was.
    if meta.get("cut") and meta["cut"] != DEFAULT_CUT_NAME:
        bits.append(cut_slug(meta["cut"]))
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


RENDER_PROFILES = ("preview", "delivery", "youtube", "match")
# Seconds of wall clock per second of finished cut. Preview is measured: a 17-shot,
# 181s cut of the Killington bin rendered in 160.8s while an Ask and a second render
# shared the machine. Delivery comes from Karl's 17-minute render of a ~2:50 cut and is
# provisional — nothing here may start one to check. Both are starting guesses that the
# first milestone replaces with measurement off the run in front of it. YouTube (the
# Export dialog's preset: 1080p at the bin's own rate, preset medium) is provisional too,
# between the two.
RENDER_S_PER_CUT_S = {"preview": 0.9, "delivery": 6.0, "youtube": 2.0, "match": 3.0}
# What share of that the join is worth. Measured at 5% on the preview above (7.9s of
# 160.8s); given more on delivery, where the join re-encodes at source resolution with
# the music mix rather than stream-copying 1080p parts. Provisional, and the reason the
# join is a milestone at all: the bar used to hit 100% the moment cutting ended.
RENDER_JOIN_SHARE = {"preview": 0.1, "delivery": 0.3, "youtube": 0.1, "match": 0.1}
# And what the review copy costs, in the same units, measured on this box against the
# 181s Killington cut with the box otherwise idle: 39.7s off the 1080p preview master
# and 95.4s off the 4K delivery one. It is a phase of the render because the players
# stream it — the render is not usable until it exists.
REVIEW_S_PER_CUT_S = {"preview": 0.22, "delivery": 0.53, "youtube": 0.25, "match": 0.4}

# ---------------------------------------------------------------- the Export dialog
#
# Verdict 2, punch 5: "Export is two buttons … Ctrl+M does nothing: there is no export
# dialog with a name, folder or size." Premiere's Export Settings name a preset, a file
# name, a location and an estimated size. Each preset here is one of assemble.py's
# profiles; `mbps` is the video bitrate at `ref` pixels (CRF encodes, so it is an
# estimate, scaled by the frame the bin actually renders at). Quick: 49.7 MB for a
# 28.1 s cut of Killington at 1080p (verdict 2) = 14 Mbps. Master: Karl's 987 MB 4K
# delivery of 181 s = 43.6 Mbps. YouTube: crf 18 at medium, provisional. `fps` is the
# rate each was measured at; the estimate scales by the square root of the rate asked
# for over it (a CRF encode spends less per frame as frames get closer together).
#
# Verdict 3, punch 10: the estimate was 64 % high on Killington (128 vs 78 MB), so a
# preset's figure is a fallback only: once the bin has a file made with it, the
# estimate is that file's measured bits per pixel per frame (`export_measured`).
#
# Every preset renders at the sequence's frame rate (`/api/project` `fps`), constant
# frame rate (assemble.py `--fps`): the journey found 23.976 exports of a 24 fps
# sequence that were not even constant. Match Sequence is also the sequence's frame
# size: what Premiere's "Match Source" gives.
EXPORT_PRESETS = {
    "quick": {"label": "Quick 1080p", "profile": "preview", "mbps": 14.0, "fps": 24000 / 1001,
              "audio_kbps": 192, "ref": (1920, 1080), "max": (1920, 1080), "fixed": True,
              "about": "H.264 · 1920×1080 at the sequence's frame rate · fast GPU encode (NVENC) when there is one, to watch the cut"},
    "match": {"label": "Match Sequence", "profile": "match", "mbps": 48.0, "fps": 30000 / 1001,
              "audio_kbps": 320, "ref": (3840, 2160), "max": (8192, 8192), "fixed": False,
              "about": "H.264 · the sequence's own frame size and frame rate · crf 18, medium encode"},
    "master": {"label": "Master 4K", "profile": "delivery", "mbps": 44.0, "fps": 30000 / 1001,
               "audio_kbps": 256, "ref": (3840, 2160), "max": (3840, 2160), "fixed": False,
               "about": "H.264 · up to 3840×2160 at the sequence's frame rate, never upscaled · slow encode"},
    "youtube": {"label": "YouTube 1080p", "profile": "youtube", "mbps": 16.0, "fps": 30000 / 1001,
                "audio_kbps": 384, "ref": (1920, 1080), "max": (1920, 1080), "fixed": False,
                "about": "H.264 · up to 1920×1080 at the sequence's frame rate · GPU encode (NVENC) when there is one · AAC 384 kbps"},
}
# a render made by the film tool (no preset) calibrates the preset of its profile
PROFILE_PRESET = {"preview": "quick", "match": "match", "delivery": "master",
                  "youtube": "youtube"}
EXPORT_NAME_MAX = 80


def export_root() -> Path:
    """`--work/renders`: every export lands in a folder under it, never anywhere else."""
    return STATE["renders"].parent


def export_component(text: str, fallback: str) -> str:
    """A file or folder name that is one path component and nothing more: no
    separators, no `..`, no leading dot, nothing a shell or a file manager trips on."""
    t = re.sub(r"\.mp4$", "", str(text or "").strip(), flags=re.I)
    t = re.sub(r"[^A-Za-z0-9 ._()-]+", "-", t)
    t = re.sub(r"\s+", " ", t).strip(" .-")[:EXPORT_NAME_MAX].strip(" .-")
    return t or fallback


def free_export_path(folder: Path, name: str) -> Path:
    """`<name>.mp4`, or `<name>-2.mp4`, `<name>-3.mp4` … — never over a file that is
    already there."""
    path = folder / f"{name}.mp4"
    n = 2
    while path.exists() or path.with_suffix(".json").exists():
        path = folder / f"{name}-{n}.mp4"
        n += 1
    return path


def media_url_for(path: Path) -> str:
    if path.parent == STATE["renders"]:
        return f"/media/render/{path.name}"
    return f"/media/export/{path.parent.name}/{path.name}"


def _native_frame() -> tuple[int, int] | None:
    """The first real clip's frame size, cached per bin: what `delivery` and `youtube`
    scale from (never up)."""
    edl = read_edl()
    for seg in edl.get("segments") or []:
        if edits.is_generated(seg.get("clip", "")):
            continue
        src = clip_path(seg["clip"])
        cache = STATE.setdefault("export_frames", {})
        if str(src) not in cache:
            cache[str(src)] = probe_resolution(src) if src.exists() else None
        if cache[str(src)]:
            return cache[str(src)]
    return None


def export_frame(preset: dict, native: tuple[int, int] | None) -> tuple[int, int]:
    mw, mh = preset["max"]
    if preset["fixed"] or not native:
        return mw, mh
    nw, nh = native
    scale = min(1.0, mw / nw, mh / nh)
    return max(2, int(nw * scale) // 2 * 2), max(2, int(nh * scale) // 2 * 2)


def export_default_name(edl: dict) -> str:
    cut = cut_name_of(edl, STATE["edl"])
    bits = [STATE["footage"].name] + ([cut] if cut and cut != DEFAULT_CUT_NAME else [])
    return export_component(" - ".join(bits), "export")


def fps_rational(fps: float | None) -> str:
    """The sequence's rate as assemble.py's `--fps` takes it: a decimal NTSC rate
    (23.976, 29.97, 59.94 …) is the /1001 rate it stands for, a whole one is itself."""
    f = float(fps or 24000 / 1001)
    if abs(f - round(f)) < 1e-6:
        return str(int(round(f)))
    ntsc = round(f * 1.001)
    if abs(f - ntsc / 1.001) < 0.002:
        return f"{ntsc * 1000}/1001"
    return f"{round(f * 1000)}/1000"


def export_rate(asked, sequence: list[dict]) -> str:
    """The rate an export is written at, as `--fps` takes it: the one the dialog showed
    (`fps` in the request: its FRAME line, from /api/export) when it is a sane rate, else
    the whole sequence's (`sequence_fps` over every shot, never only a range's — an
    In/Out export of a 29.97 sequence that starts on a 59.94 clip said 29.970 fps and
    wrote 59.94, verdict 4)."""
    v = _rate_value(asked) if asked is not None else None
    if v is not None and 1.0 <= v <= 240.0:
        return fps_rational(v)
    return fps_rational(sequence_fps(sequence))


def _rate_value(text: str | None) -> float | None:
    try:
        num, _, den = str(text).partition("/")
        return float(num) / float(den or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


EXPORT_CALIBRATE_N = 3          # the newest files of a preset that set its estimate


def export_measured(key: str, seq_fps: float) -> tuple[float | None, int]:
    """Bits per pixel per frame of the newest files this bin made with preset `key`
    (an export names it; a film-tool render counts for its profile's preset), as the
    median of up to EXPORT_CALIBRATE_N, and how many there were. Only files that are
    still on disk, with their length and frame on record."""
    root = export_root()
    rows = []
    if root.exists():
        for meta_path in root.glob("*/*.json"):
            try:
                m = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(m, dict):
                continue
            k = m.get("preset") or PROFILE_PRESET.get(m.get("profile") or "preview")
            if k != key:
                continue
            # this bin's: an export says which; an older render is in the bin's folder
            if m.get("footage", STATE["footage"].name if meta_path.parent == STATE["renders"]
                     else None) != STATE["footage"].name:
                continue
            mp4 = meta_path.with_suffix(".mp4")
            dur, w, h = m.get("duration_s"), m.get("width"), m.get("height")
            if not (mp4.exists() and dur and w and h):
                continue
            fps = _rate_value(m.get("fps")) or (24000 / 1001 if m.get("profile") == "preview"
                                                else seq_fps)
            bits = mp4.stat().st_size * 8.0
            rows.append((float(m.get("created") or 0), bits / (float(dur) * w * h * fps)))
    rows.sort(reverse=True)
    vals = sorted(v for _, v in rows[:EXPORT_CALIBRATE_N])
    if not vals:
        return None, 0
    mid = len(vals) // 2
    bppf = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
    return bppf, len(vals)


# ---------------------------------------------------- the first export's size (punch 6)
#
# Verdict 4: "Estimate 310 MB, file 148 MB (2.1×)" — a preset's typical bitrate is a
# guess until the bin has a file made with it. So the dialog's first open encodes a ~2 s
# sample of the sequence at the preset (assemble.py, the export's own profile and rate,
# the sequence's shots), cached per preset and bin, and the estimate scales it. The
# sample is a few short slices spread over the film, so one snowfield does not stand for
# a cut of lift lines; and it is measured frame by frame — the mean keyframe and the mean
# other frame, both scaled to the preset's frame — because every shot of an export
# starts on a keyframe, and a 2 s slice is far more keyframe than a 10 s shot is. The
# dialog lays those over the range it exports: one keyframe per shot plus one per
# X264_KEYINT frames, every other frame a mean one, plus the sound at its bitrate.
#
# Eight slices of a quarter second, not fewer longer ones: measured on 40 s of Killington
# (6 shots, Quick 1080p, real file 7.12 Mbps of video) the model read 7.95 Mbps from 8
# slices (+12 %), 7.32 from 6 (+3 %), but 2.57 from 4 (−64 %) and 10.62 from 2 (+49 %):
# GoPro detail swings shot to shot, so coverage beats length. The preset's typical
# figure said 14 Mbps (+97 %). On the suite's synthetic clips 4 slices read 3.14 against
# a real 3.12.
EXPORT_SAMPLE_S = 2.0
EXPORT_SAMPLE_SLICES = 8
EXPORT_SAMPLE_TIMEOUT_S = 600.0
X264_KEYINT = 250                         # x264's default -g: a keyframe at least this often
SAMPLES: dict[str, dict] = {}             # "<bin>.<preset>" → {"state", "scope", "error"}
SAMPLES_LOCK = threading.Lock()


def sample_dir() -> Path:
    return STATE["work"] / "export-samples"


def sample_key(preset: str) -> str:
    return f"{STATE['footage'].name}.{preset}"


def sample_file(preset: str) -> Path:
    return sample_dir() / f"{sample_key(preset)}.json"


def load_sample(preset: str) -> dict | None:
    try:
        d = json.loads(sample_file(preset).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("p_bytes") else None


def sample_segments(segments: list[dict], seconds: float = EXPORT_SAMPLE_S,
                    slices: int = EXPORT_SAMPLE_SLICES) -> list[dict]:
    """`slices` pieces of the film, `seconds` in all, centred at evenly spaced film
    times: each a range of the shot playing there (its own clip, speed and controls),
    cut to that shot's edges. A film shorter than `seconds` is sampled whole."""
    durs = [edits.dur(x) for x in segments]
    total = sum(durs)
    if total <= 0:
        return []
    if total <= seconds:
        return [dict(x) for x in segments]
    n = max(1, min(slices, len(segments) * 2))
    each = seconds / n
    out = []
    for k in range(n):
        t = total * (k + 0.5) / n
        start = 0.0
        for seg, d in zip(segments, durs):
            if start + d >= t or seg is segments[-1]:
                sp = edits.speed_of(seg)
                a = max(0.0, min(d - each, t - start - each / 2))
                cin = float(seg["in"]) + a * sp
                cout = min(float(seg["out"]), cin + each * sp)
                piece = {k2: v for k2, v in seg.items() if k2 not in ("id", "a_off")}
                piece.update({"in": round(cin, 4), "out": round(cout, 4)})
                keys = (seg.get("controls") or {}).get("keys")
                if keys and a > 0:
                    # keyframes count from the shot's start: the slice starts `a` in
                    piece["controls"] = {**seg["controls"], "keys": {
                        p: [[round(float(t) - a, 4), v] for t, v in ks]
                        for p, ks in keys.items()}}
                out.append(piece)
                break
            start += d
    return out


def _probe_frames(path: Path) -> tuple[list[int], list[int]]:
    """The video packets' sizes: (keyframes, every other frame)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "packet=size,flags", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    keys, rest = [], []
    for line in r.stdout.splitlines():
        size, _, flags = line.partition(",")
        try:
            n = int(size)
        except ValueError:
            continue
        (keys if "K" in flags else rest).append(n)
    return keys, rest


def _sample_job(preset: str, key: str, scope: procscope.Scope) -> None:
    rec = SAMPLES[key]
    work = sample_dir() / f"tmp-{key}"
    try:
        edl = read_edl()
        segs = sample_segments(edl.get("segments") or [])
        if not segs:
            raise RuntimeError("the sequence is empty")
        p = EXPORT_PRESETS[preset]
        seq = edl.get("segments") or []
        rate = export_rate(None, seq)
        sample = {k: v for k, v in edl.items()
                  if k not in ("overlays", "effects_music", "tracks", "markers", "in", "out")}
        sample["segments"] = segs
        work.mkdir(parents=True, exist_ok=True)
        edl_path, out = work / "sample.json", work / "sample.mp4"
        edl_path.write_text(json.dumps(sample), encoding="utf-8")
        cmd = ["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
               "--footage", str(tool_footage()), "--sidecars", str(STATE["sidecars"]),
               "--assets", str(STATE["assets"]), "--profile", p["profile"],
               "--colour-dir", str(colour_dir()), "--generated-dir", str(generated_dir()),
               "--parts-dir", str(work / "parts"), "--fps", rate, "-o", str(out),
               # every slice at once (verdict 6, punch 7): a slice is a quarter second, so
               # what it waits on is its ffmpeg's start and seek, not the cores. Measured,
               # Killington's sample at load ~25: Quick 43 → 12 s, Match 4K 80 → 30 s.
               "--jobs", str(max(1, len(segs)))]
        t0 = time.time()
        with procscope.active(scope):
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=EXPORT_SAMPLE_TIMEOUT_S)
        if scope.cancelled:
            rec.update(state="cancelled")
            return
        if r.returncode != 0 or not out.exists():
            raise RuntimeError((r.stderr or r.stdout or "").strip()[-200:] or "no sample")
        keys, rest = _probe_frames(out)
        res = probe_resolution(out)
        # the sound as the encoder really spends it: AAC at "384k" on quiet or simple
        # sound comes in well under, and on a small frame the sound is most of the file
        ab = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                             "-show_entries", "stream=bit_rate", "-of", "csv=p=0", str(out)],
                            capture_output=True, text=True).stdout.strip().strip(",")
        if not keys or not res:
            raise RuntimeError("the sample has no frames")
        frames = keys + rest
        data = {
            "preset": preset, "bin": STATE["footage"].name, "created": time.time(),
            "seconds": round(sum(edits.dur(x) for x in segs), 3), "slices": len(segs),
            "fps": rate, "width": res[0], "height": res[1], "frames": len(frames),
            "keys": len(keys), "i_bytes": sum(keys) / len(keys),
            # a sample with no frame but keyframes (a very short film) has only the
            # keyframes' mean to go on
            "p_bytes": (sum(rest) / len(rest)) if rest else sum(keys) / len(keys),
            "encode_s": round(time.time() - t0, 2),
            "audio_kbps": round(int(ab) / 1000, 1) if ab.isdigit() else None,
        }
        sample_dir().mkdir(parents=True, exist_ok=True)
        sample_file(preset).write_text(json.dumps(data, indent=1), encoding="utf-8")
        rec.update(state="done")
    except (RuntimeError, OSError, subprocess.TimeoutExpired, KeyError) as exc:
        rec.update(state="cancelled" if scope.cancelled else "failed",
                   error=str(exc)[:300])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def start_sample(preset: str) -> dict:
    """Encode this preset's sample unless it is cached or already being made."""
    key = sample_key(preset)
    if load_sample(preset):
        return {"state": "done"}
    with SAMPLES_LOCK:
        rec = SAMPLES.get(key)
        if rec and rec["state"] == "running":
            return {"state": "running"}
        scope = procscope.Scope(f"export sample {key}")
        rec = SAMPLES[key] = {"state": "running", "scope": scope, "error": None,
                              "started": time.time()}
    threading.Thread(target=_sample_job, args=(preset, key, scope), daemon=True).start()
    return {"state": "running"}


def stop_samples() -> int:
    """Kill every sample being encoded (an export starting wants the cores)."""
    with SAMPLES_LOCK:
        live = [r for r in SAMPLES.values() if r["state"] == "running"]
    for r in live:
        r["scope"].kill(grace_s=0.5)
    return len(live)


def sample_state(preset: str) -> str:
    if load_sample(preset):
        return "done"
    rec = SAMPLES.get(sample_key(preset))
    return rec["state"] if rec else "none"


def sample_model(preset: str, w: int, h: int) -> dict | None:
    """The sample's frame sizes at the preset's frame (`w`×`h`), for the dialog."""
    d = load_sample(preset)
    if not d:
        return None
    k = (w * h) / max(1, int(d["width"]) * int(d["height"]))
    return {"i_bytes": round(float(d["i_bytes"]) * k, 1),
            "p_bytes": round(float(d["p_bytes"]) * k, 1), "keyint": X264_KEYINT,
            "seconds": d.get("seconds"), "slices": d.get("slices"),
            "audio_kbps": d.get("audio_kbps")}


def model_video_mbps(model: dict, secs: float, shots: int, fps: float) -> float:
    """The sample's model over `secs` of film in `shots` shots, as a video bitrate."""
    frames = max(1.0, secs * fps)
    keys = min(frames, max(1, shots) + math.floor(frames / model["keyint"]))
    return (keys * model["i_bytes"] + (frames - keys) * model["p_bytes"]) * 8 / secs / 1e6


@app.post("/api/export/sample")
async def api_export_sample(request: Request) -> JSONResponse:
    """Start the preset's size sample (the dialog asks on its first open)."""
    body = await request.json() if int(request.headers.get("content-length") or 0) else {}
    preset = str((body or {}).get("preset") or "quick")
    if preset not in EXPORT_PRESETS:
        raise HTTPException(400, f"unknown export preset: {preset!r}")
    if not (read_edl().get("segments") or []):
        return JSONResponse({"preset": preset, "state": "none"})
    return JSONResponse({"preset": preset, **start_sample(preset)})


@app.get("/api/export/sample")
def api_export_sample_status(preset: str = "quick") -> JSONResponse:
    if preset not in EXPORT_PRESETS:
        raise HTTPException(400, f"unknown export preset: {preset!r}")
    rec = SAMPLES.get(sample_key(preset)) or {}
    return JSONResponse({"preset": preset, "state": sample_state(preset),
                         "error": rec.get("error"), "sample": load_sample(preset)})


def export_folders() -> list[dict]:
    """The folders under `--work/renders`, the most recently written first: name, how
    many films are in it and when the newest was made."""
    root = export_root()
    out = []
    if root.exists():
        for d in root.iterdir():
            if not d.is_dir() or d.name.startswith("."):
                continue
            films = [f for f in d.glob("*.mp4") if not f.name.startswith(".")]
            when = max([f.stat().st_mtime for f in films] or [d.stat().st_mtime])
            out.append({"name": d.name, "films": len(films), "when": round(when, 1)})
    out.sort(key=lambda r: (-r["when"], r["name"]))
    return out


@app.get("/api/export")
def api_export() -> JSONResponse:
    """What the Export dialog offers: the presets (with the frame and rate each renders
    at and its bitrate, for the size estimate — measured off this bin's own earlier
    files of that preset when there are any), the default file name (from the cut's
    name), the default folder (the bin's own, where the versions list looks) and the
    folders already under `--work/renders`, the most recently used first."""
    edl = read_edl()
    native = _native_frame()
    seq = edl.get("segments") or []
    seq_fps = sequence_fps(seq)
    seq_s = sum(edits.dur(x) for x in seq)
    presets = []
    for key, p in EXPORT_PRESETS.items():
        w, h = export_frame(p, native)
        rw, rh = p["ref"]
        bppf, n = export_measured(key, seq_fps)
        model = None if bppf else sample_model(key, w, h)
        audio_kbps = p["audio_kbps"]
        if bppf:
            total = bppf * w * h * seq_fps / 1e6
            mbps = max(0.05, total - p["audio_kbps"] / 1000)
            source = "measured"
        elif model and seq_s > 0:
            mbps = model_video_mbps(model, seq_s, len(seq), seq_fps)
            source = "sample"
            audio_kbps = model.get("audio_kbps") or audio_kbps
        else:
            mbps = max(0.5, p["mbps"] * (w * h) / (rw * rh) * math.sqrt(seq_fps / p["fps"]))
            source = "typical"
        presets.append({"key": key, "label": p["label"], "profile": p["profile"],
                        "about": p["about"], "width": w, "height": h,
                        "fps": round(seq_fps, 3), "fps_rational": fps_rational(seq_fps),
                        "video_mbps": round(mbps, 3), "audio_kbps": audio_kbps,
                        "measured": n, "source": source, "sample": model,
                        "sample_state": "done" if model else sample_state(key)})
    root = export_root()
    folders = export_folders()
    return JSONResponse({
        "presets": presets, "preset": "quick", "name": export_default_name(edl),
        "folder": STATE["footage"].name, "folders": [f["name"] for f in folders],
        "recent": folders[:6], "root": str(root), "fps": round(seq_fps, 3),
    })


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
    # The Export dialog (verdict 2 punch 5) names a preset, a file name and a folder;
    # the film tool's two buttons still send a bare profile and get `cut_<job>.mp4`.
    preset_key = body.get("preset")
    export = preset_key is not None or body.get("name") is not None
    if export:
        if preset_key is None:
            preset_key = "quick"
        if preset_key not in EXPORT_PRESETS:
            raise HTTPException(400, f"unknown export preset: {preset_key!r}")
        profile = EXPORT_PRESETS[preset_key]["profile"]
    else:
        profile = body.get("profile", "preview")
    if profile not in RENDER_PROFILES:
        raise HTTPException(400, f"unknown render profile: {profile!r}")
    if not body.get("segments"):
        raise HTTPException(400, "nothing to export: the sequence is empty")
    stop_samples()            # the size samples are the dialog's; an export wants the cores
    edl = read_edl()
    # The sequence's rate, from the whole sequence before `segments` is replaced by what
    # the body sends (an In/Out range sends only the shots inside it): the rate the
    # dialog's FRAME line shows, for any range (verdict 4, punch 6).
    seq_rate = export_rate(body.get("fps"), edl.get("segments") or body["segments"])
    # Effect Controls ride each segment to the render (verdict 3, punch 3), validated by
    # the same rule a save uses, so the film is what the Program monitor showed.
    segments = []
    for s in body["segments"]:
        if not isinstance(s, dict):
            raise HTTPException(400, "a segment must be an object")
        s = dict(s)
        try:
            c = clean_controls(s.get("controls"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"controls: {exc}")
        if c:
            s["controls"] = c
        else:
            s.pop("controls", None)
        segments.append(s)
    edl["segments"] = segments
    job = uuid.uuid4().hex[:8]
    edl_path = STATE["work"] / f"render_{job}.json"
    if export:
        # One folder under --work/renders and one file name — sanitised to a single
        # path component each, so no request can write anywhere else.
        folder = export_root() / export_component(body.get("folder"), STATE["footage"].name)
        folder.mkdir(parents=True, exist_ok=True)
        out_path = free_export_path(
            folder, export_component(body.get("name"), export_default_name(edl)))
    else:
        out_path = STATE["renders"] / f"cut_{job}.mp4"
    edl_path.write_text(json.dumps(edl, indent=1), encoding="utf-8")
    meta = {
        "job": job, "created": time.time(), "title": edl.get("title", ""),
        "segments": len(edl["segments"]),
        "planned_s": round(sum(edits.dur(s) for s in edl["segments"]), 2),
        "story": (edl.get("story") or "")[:300],
        "note": (body.get("label") or "")[:120],
        "music": (edl.get("effects_music") or {}).get("asset"),
        "profile": profile,
        # Which cut of the bin this came from, now that a bin can have several.
        "cut": cut_name_of(edl, STATE["edl"]),
        # The shot list this file was made from, so the board can say which version is
        # the cut currently on the timeline. Karl watched a rendered *proposal* and
        # reported that the board "doesn't seem to reflect the render" — it did not,
        # and nothing on screen said which of the renders it did reflect.
        "shots": [{"clip": s["clip"], "in": s["in"], "out": s["out"],
                   **({"speed": s["speed"]} if s.get("speed") else {}),
                   **({"controls": s["controls"]} if s.get("controls") else {})}
                  for s in edl["segments"]],
        "footage": STATE["footage"].name,
    }
    if export:
        # An export is constant frame rate at the sequence's rate, whatever the preset.
        meta.update(export=True, preset=preset_key,
                    preset_label=EXPORT_PRESETS[preset_key]["label"],
                    fps=seq_rate)
    shots = len(edl["segments"])
    planned = meta["planned_s"]
    RENDERS[job] = progress.Job(
        "render", (f"Exporting — {EXPORT_PRESETS[preset_key]['label']}" if export
                   else f"Rendering — {profile}"), id=job, stage="cutting", log="",
        output=None, url=None, done=0, total=shots, profile=profile,
        planned_s=planned, detail=f"{shots} shots, {planned:.0f}s",
        progress_pct=0.0, encoded_s=0.0, path=str(out_path), export=export,
        preset=preset_key if export else None)
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
    # An export makes no review copy inside its job (the file asked for is the
    # deliverable), so its bar is cutting and joining only.
    review_s = 0.0 if export else REVIEW_S_PER_CUT_S[profile]
    stages = [progress.milestone("cutting", "cutting the shots", per_s * (1.0 - join)),
              progress.milestone("joining", "joining and mixing", per_s * join)]
    if not export:
        stages.append(progress.milestone("review", "making the review copy", review_s))
    RENDERS[job].set_estimate(max(5.0, planned * (per_s + review_s)), stages,
                              source="measured")
    threading.Thread(target=_render_job, args=(job, edl_path, out_path, meta),
                     daemon=True).start()
    if not export:
        return JSONResponse({"job": job})
    return JSONResponse({"job": job, "path": str(out_path), "name": out_path.name,
                         "folder": out_path.parent.name, "preset": preset_key,
                         "planned_s": planned})


@app.post("/api/render/{job}/cancel")
def api_render_cancel(job: str) -> JSONResponse:
    """Stop a running render: its whole process group, and nothing is left on disk."""
    entry = RENDERS.get(job)
    if entry is None:
        raise HTTPException(404, "no such job")
    if entry["state"] in progress.TERMINAL:
        return JSONResponse({"job": job, "state": entry["state"]})
    entry["cancelled"] = True
    proc = RENDER_PROCS.get(job)
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, 15)
        except (ProcessLookupError, PermissionError):
            pass
    return JSONResponse({"job": job, "state": "cancelling"})


@app.get("/api/renders")
def api_renders() -> JSONResponse:
    """Every cut rendered for this project, newest first.

    The board showed only the newest render, so comparing two versions meant finding
    mp4s on disk — and comparison is how you actually judge an edit. Reacting to a
    choice is faster and more informative than judging a single artifact.
    """
    out = []
    res_cache: dict = STATE.setdefault("render_res_cache", {})
    for mp4 in STATE["renders"].glob("*.mp4"):
        # An export to the bin's own folder is a film like any other; a file still
        # being written (hidden, `.partial`) or mid-mix (`.scored`) is not.
        if mp4.name.startswith(".") or ".scored." in mp4.name:
            continue
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
            # The cut it was made from (a bin has several); None on renders made
            # before a bin could, which the row then simply does not name
            "cut": meta.get("cut"),
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


def ranged_file(path: Path, request: Request, mime: str | None = None) -> Response:
    """Serve a file with byte-range support.

    Required, not a nicety: without a 206 path the <video> element cannot seek, so
    every segment preview would have to stream from the start of the clip.
    """
    if not path.exists():
        raise HTTPException(404, f"not found: {path.name}")
    size = path.stat().st_size
    mime = mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
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
    """The clip's 720p proxy — or, until it exists, something the browser can play now.

    Verdict 1 (punch 5): the monitor showed a raw MEDIA_ELEMENT_ERROR for most of the
    first hour, because this 404'd until a proxy finished at ~8 min a clip. Now a clip
    that is in the bin redirects to its original when the browser can decode that, or
    to its 360p preview (built first, in seconds), waiting up to PREVIEW_WAIT_S for one.
    A redirect rather than the bytes under this URL: a <video> keeps range-requesting
    the URL it was redirected to, so when the proxy lands mid-play no player is handed
    another file's bytes at the same offsets. A 503 with retry-after only when even the
    preview is not ready; `/media/status/<stem>.mp4` says when it is. Between the preview
    and the proxy, a clip the cut plays redirects to its range proxy (`x-roughcut-media:
    range`, wave 7): the cut's shots sharp at 1280 on the clip's own timeline.

    Verdict 8 (blocker 2): this held the request PREVIEW_WAIT_S (20 s) for a preview still
    being made, and the board's wait (poster + "preparing preview…") only starts on the
    error that answer gives: a black, wordless Program for 20 s a shot. Now it answers
    within PROXY_ANSWER_S: the best thing that plays now (proxy, a playable original, a
    range proxy, the preview) or, while even the preview is being made, a 503 at once
    with `retry-after` (the preview's estimate, also in `x-roughcut-eta`). Not a 307 to
    the original of such a clip: `browser_playable` already said no (4K HEVC Chrome does
    not decode here, or 4K H.264 at ~60 Mb/s that stalls the Program on the viewer's CPU
    and over the laptop's tunnel); the preview, asked for now at normal priority, plays
    sooner than that original would, and the board plays past the shot on its poster
    until it lands (as Premiere plays past offline media)."""
    path = STATE["proxy_dir"] / Path(name).name
    if path.exists():
        return ranged_file(path, request)
    stem = Path(name).stem
    clip = clip_for_stem(stem) if Path(name).suffix.lower() == ".mp4" else None
    if clip is None:
        return ranged_file(path, request)          # not a clip of this bin: the 404
    want_media(stem)
    kind, url = media_fallback(clip, wait_s=PROXY_ANSWER_S)
    if kind == "proxy":
        return ranged_file(path, request)
    if url:
        return Response(status_code=307, headers={
            "location": url, "cache-control": "no-store", "x-roughcut-media": kind})
    eta = preview_eta(clip) if kind == "building" else None
    if kind == "building":
        hold_media(clip)                 # a monitor waits on this build: never paused
    raise HTTPException(503, f"{stem}: a playable preview is still being made",
                        headers={"retry-after": str(max(1, min(10, math.ceil(eta or 5)))),
                                 "x-roughcut-eta": "" if eta is None else f"{eta:.0f}",
                                 "x-roughcut-media": kind, "cache-control": "no-store"})


@app.get("/media/render/{name}")
def media_render(name: str, request: Request) -> Response:
    return ranged_file(STATE["renders"] / Path(name).name, request)


@app.get("/media/export/{folder}/{name}")
def media_export(folder: str, name: str, request: Request) -> Response:
    """An export in a folder other than the bin's own, for the dialog's Play."""
    root = export_root().resolve()
    path = (root / Path(folder).name / Path(name).name).resolve()
    if path.parent.parent != root or path.suffix.lower() != ".mp4":
        raise HTTPException(404, "not found")
    return ranged_file(path, request)


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
    # PICTURE_V in the name: a poster cut from a v1 preview of a rotation-flagged clip is
    # upside down, and an old cache must be rebuilt rather than served (verdict 3 punch 1).
    return STATE["posters"] / f"{stem}@{t:09.2f}.{PICTURE_V}.jpg"


def build_poster(src: Path, dest: Path, t: float, original: bool = False) -> None:
    """One frame out of a proxy (or, before there is one, the original), small, on disk.

    `-ss` ahead of `-i` so ffmpeg seeks to the timestamp instead of decoding its way
    there — 188 s into a Killington proxy is ~80 ms that way and seconds the other.
    Temp name and rename for the reason build_proxy has one: the server is serving
    while this runs, and two boards can share a work dir.

    From an `original` it is the proxy's frame: the same rotation rule and HDR tone map
    build_proxy applies, decoded on the GPU when there is one (a 4K HEVC GoPro frame:
    0.8 s that way, 1.8 s on a loaded CPU; byte-identical JPEG either way). Only
    `-hwaccel cuda`, never `-hwaccel_output_format cuda`: the frames come back to system
    memory, where ffmpeg's autorotate turns them as it does for the proxy and the export
    (on CUDA frames it silently does not — verdict 3's upside-down previews). A proxy or
    preview source is already upright and carries no rotation tag.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(f".{os.getpid()}.{threading.get_ident()}.part.jpg")
    err = ""
    pre: list[str] = []
    vf = f"scale=min({POSTER_W}\\,iw):-2"
    decoders: list[list[str]] = [[]]
    if original:
        if STATE.get("orient") == "none":
            pre = ["-display_rotation", "0"]
        norm = source_normalise(src)
        if norm:
            vf = f"{norm},{vf}"
        f = media_facts(src) or {}
        # only where it pays: CUDA's start-up is ~0.5 s, more than a 1080p h264 frame
        if preview_gpu() and (f.get("vcodec") == "hevc"
                              or (f.get("width") or 0) * (f.get("height") or 0) > 1920 * 1080):
            decoders = [["-hwaccel", "cuda"], []]
    # A trim can park an in-point past the end of a clip that was re-proxied shorter;
    # a card showing the first frame beats a card showing a broken image.
    for ss in dict.fromkeys((max(0.0, t), 0.0)):
        for dec in decoders:
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-nostdin", *pre, *dec, "-ss", f"{ss:.3f}",
                 "-i", str(src), "-frames:v", "1",
                 "-vf", vf, "-q:v", str(POSTER_Q),
                 "-f", "image2", str(tmp)],
                capture_output=True, text=True)
            if r.returncode == 0 and tmp.exists() and tmp.stat().st_size:
                tmp.replace(dest)
                return
            err = r.stderr[-200:]
            tmp.unlink(missing_ok=True)
    raise RuntimeError(f"poster failed for {src.name} at {t:.2f}s: {err}")


def nearest_poster(stem: str, t: float) -> tuple[Path, float] | None:
    """The poster of `stem` already on disk whose time is nearest `t` (any time), or None."""
    best: tuple[float, Path, float] | None = None
    tail = f".{PICTURE_V}.jpg"
    for p in STATE["posters"].glob(f"{stem}@*{tail}"):
        try:
            pt = float(p.name[len(stem) + 1: -len(tail)])
        except ValueError:
            continue
        d = abs(pt - t)
        if best is None or d < best[0]:
            best = (d, p, pt)
    return (best[1], best[2]) if best else None


@app.get("/media/poster/{name}")
def media_poster(name: str, t: float = 0.0, near: int = 0) -> Response:
    """A still frame from a proxy, at a shot's in-point.

    The shot cards used to be `<video preload="metadata">` elements pointed at the
    proxies. Sixteen of those plus two render previews is eighteen streams against
    Chrome's six-connections-per-host limit, and the monitor's own request queued
    behind them: measured on the Killington board from `playFrom(0)`, the live element
    took 6.8–9.5 s to reach readyState 4 and showed black (mean pixel 0.0) the whole
    time while the audio played — Karl's *"I can hear the videos... but the preview
    window still shows up blank"*. A card needs a picture, not a stream. This is a few
    kilobytes, cached on disk under --work and immutable for a given clip and time.

    `near=1` (the Program monitor's ask, wave 13 CANVAS2): when this frame is not on disk
    and would be cut from the ORIGINAL (no proxy or preview yet: a 4K GoPro frame is
    0.7-1.3 s on a loaded box, every seek a new time), the clip's poster already on disk
    nearest `t` is answered at once instead, not cached (`no-store`, `x-roughcut-poster:
    near <its t>`): the shot shows a picture of itself within a request, not black, while
    the monitor asks for the exact frame beside it. Otherwise it is the exact frame.
    """
    stem = Path(name).stem
    if stem.endswith(f".{PICTURE_V}"):          # poster_url's versioned name
        stem = stem[: -len(PICTURE_V) - 1]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", stem):
        raise HTTPException(404, f"no such clip: {name}")
    if not math.isfinite(t) or t < 0:
        t = 0.0
    t = round(min(t, 86400.0), 2)
    dest = poster_path(stem, t)
    if not dest.exists() and near:
        found = poster_source(stem)
        alt = nearest_poster(stem, t) if found is not None and found[1] else None
        if alt is not None:
            return FileResponse(alt[0], media_type="image/jpeg",
                                headers={"cache-control": "no-store",
                                         "x-roughcut-poster": f"near {alt[1]:.2f}"})
    if not dest.exists():
        try:
            # the board's own: never paused for playback (not background), but in a
            # scope so its ffmpeg dies with the server
            with procscope.active(poster_scope()):
                ensure_poster(stem, t)
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc
    if not dest.exists():
        raise HTTPException(404, f"no such clip: {stem}")
    return FileResponse(dest, media_type="image/jpeg", headers=POSTER_CACHE)


def poster_source(stem: str) -> tuple[Path, bool] | None:
    """Where a poster's frame comes from, and whether that is an original: the proxy,
    a generated clip (INTAKE M13: its own proxy; `gen_` stems cannot collide with
    footage, so the poster cache shares the dir), the 360p preview, else the original.

    Verdict 1 (punch 5): this used to 404 until the proxy existed — ~8 min a clip on
    Killington — so every bin card was a broken image with its alt text. A frame out of
    the original costs a second; a card with no picture costs the first impression."""
    proxy = STATE["proxy_dir"] / f"{stem}.mp4"
    if proxy.exists():
        return proxy, False
    if edits.is_generated(stem):
        gen = generated_dir() / f"{stem}.mp4"
        return (gen, False) if gen.exists() else None
    clip = clip_for_stem(stem)
    if clip is None:
        return None
    prev = preview_path(stem)
    if prev.exists():
        return prev, False
    return clip_path(clip), True


POSTER_BUILDING: dict[str, threading.Event] = {}     # dest -> set when that build ends
# The open-time walk's own two slots: it starts the moment a bin opens, so by the time
# the cards ask, most of their t=0 posters are on disk or one build away — and a card
# asking for a poster the walk is making waits for that build instead of starting a
# second (POSTER_BUILDING). Two more ffmpegs than before, only in a bin's first seconds.
WALK_POSTER_SLOTS = threading.BoundedSemaphore(2)


def ensure_poster(stem: str, t: float, slots: threading.BoundedSemaphore | None = None,
                  ) -> Path | None:
    """The poster for `stem` at `t` on disk (built if missing), or None for no clip."""
    dest = poster_path(stem, t)
    if dest.exists():
        return dest
    found = poster_source(stem)
    if found is None:
        return None
    src, original = found
    key = str(dest)
    with MEDIA["lock"]:
        ev = POSTER_BUILDING.get(key)
        owner = ev is None
        if owner:
            ev = POSTER_BUILDING[key] = threading.Event()
    if not owner:
        with media_waiting(key):        # a paused (playback) walk build resumes for it
            ev.wait(30)
        if dest.exists():
            return dest
    try:
        with slots or POSTER_SLOTS:
            if not dest.exists():      # another request may have built it while we waited
                with procscope.tagged(key):
                    build_poster(src, dest, t, original=original)
    finally:
        if owner:
            with MEDIA["lock"]:
                POSTER_BUILDING.pop(key, None)
            ev.set()
    return dest


# ---------------------------------------------------------------- picture before proxies
#
# Verdict 1, punch 5: "You can't see your footage when you open it." The 720p proxy is
# the right thing to edit on, and on a loaded box it is ~8 min a clip of 4K60 HEVC —
# decoding the master is the cost, not the encode. So before it, every clip gets
# something the browser can play now:
#
#   * the original itself, when the browser can decode it (h264/aac in mp4 at ≤1080p,
#     an ordinary phone or screen recording): no work at all, one cached ffprobe;
#   * otherwise a 360p preview with a keyframe every half second (cheap seeks), built
#     first and clip by clip: decoded and encoded on the GPU when there is one —
#     measured on a Killington GoPro clip under load 10: 20 s of footage in 4.0 s wall
#     and 1.5 s of CPU, against 21 s wall for the same span of 720p proxy, so it does
#     not take the CPU from the pass. Without a GPU it is libx264 ultrafast, and only
#     for the clips the human opens (on demand), never as a background walk.
#
# The clips the human asks for go first — in this queue and in ensure_proxies'.

PREVIEW_H = 360                 # the preview's short edge
PREVIEW_GOP_S = 0.5             # a keyframe every half second: a seek decodes ≤ that
PREVIEW_WAIT_S = 20.0           # (before verdict 8) how long /media/proxy held a request
PROXY_ANSWER_S = 0.3            # /media/proxy holds a building clip this long (a preview landing in it is served)
HOLD_S = 8.0                    # a monitor told "building" keeps that build unpaused this long
PREVIEW_RATE = {True: 0.25, False: 1.0}   # build s per footage s before one is measured (GPU, CPU)
PREVIEW_SLOTS = threading.BoundedSemaphore(2)   # one background + one asked-for at once
PLAYABLE_MAX_EDGE = 1920        # an original past this goes through a preview anyway
PLAYABLE_MAX_BPS = 25_000_000   # ...and past this bitrate (it streams to a laptop)
BROWSER_VIDEO = {".mp4": {"h264"}, ".m4v": {"h264"}, ".mov": {"h264"},
                 ".webm": {"vp8", "vp9", "av1"}}
BROWSER_AUDIO = {".mp4": {"aac", "mp3"}, ".m4v": {"aac", "mp3"}, ".mov": {"aac", "mp3"},
                 ".webm": {"opus", "vorbis"}}
MEDIA: dict = {"lock": threading.Lock(), "wanted": [], "building": {}, "failed": {},
               "facts": {}, "thread": None, "gpu": None,
               # Stop means stop for the pictures too (verdict 4, punch 8): every preview
               # and poster encode runs in this scope, and the index's cancel kills it
               # (`stop_media`); `walk_gen` moves on so the background walk ends.
               "scope": None, "walk_gen": 0, "stopped": 0,
               # set to wake the walk early (a cut saved): it looks at the cut at once
               "kick": threading.Event()}


def media_scope(asked: bool = False) -> procscope.Scope:
    """The scope preview and poster encodes run in: a fresh one after a stop. The walk's
    work is background (nice 19, paused while the Program plays); a preview a monitor
    asked for (`start_preview`: somebody is looking at "preparing preview…") is not."""
    slot = "ask_scope" if asked else "scope"
    with MEDIA["lock"]:
        sc = MEDIA.get(slot)
        if sc is None or sc.cancelled:
            sc = MEDIA[slot] = procscope.Scope("asked previews" if asked else "previews",
                                               background=not asked)
        return sc


def stop_media() -> int:
    """Kill every preview and poster ffmpeg (their process groups) and end the
    background walk. An asked-for clip (a monitor waiting on it) is built again when it
    is next asked for; a killed build is not recorded as failed. Returns how many
    processes were signalled."""
    with MEDIA["lock"]:
        MEDIA["walk_gen"] += 1
        sc, MEDIA["scope"] = MEDIA.get("scope"), None
        ask, MEDIA["ask_scope"] = MEDIA.get("ask_scope"), None
    n = sum(x.kill() for x in (sc, ask) if x is not None)
    MEDIA["stopped"] += n
    return n


# ---------------------------------------------------------------- yield to playback
#
# Verdict 6, punch 3: the app's own proxy / preview / range / close-look / poster-walk
# ffmpeg ran beside the Program on the same box, and the cut's holes grew with the load
# they made. Now every such encode runs at nice 19 (procscope's background scopes), and
# while any board's Program plays (POST /api/playing {on: true} on play and every
# PLAY_BEAT_S while it plays, {on: false} on pause) the server SIGSTOPs those ffmpegs and
# SIGCONTs them on pause, or PLAY_LAPSE_S after the last beat (a closed tab, a crashed
# browser: work can never stay stopped for good). Never paused: the export (its own
# group, no scope), a poster the board asked for (`poster_scope`, not background), and a
# background build somebody is waiting on right now (`media_waiting`: a monitor holding
# /media/proxy for its preview, a card waiting on the walk's poster).

PLAY_LAPSE_S = 5.0          # a playing board that stops beating is taken as paused
YIELD_TICK_S = 0.2          # how often the yield thread looks (a new encode, a lapse)
PLAYING: dict = {"lock": threading.Lock(), "tabs": {}, "stopped": {}, "thread": None,
                 "log": []}


@contextmanager
def media_waiting(key: str):
    """Somebody (a request) is waiting on the background build of `key` for this block:
    the yield thread leaves that build running (or resumes it) while the Program plays."""
    with MEDIA["lock"]:
        w = MEDIA.setdefault("waiters", {})
        w[key] = w.get(key, 0) + 1
    try:
        yield
    finally:
        with MEDIA["lock"]:
            w = MEDIA["waiters"]
            w[key] -= 1
            if w[key] <= 0:
                w.pop(key, None)


def _waited_on() -> set[str]:
    now = time.monotonic()
    with MEDIA["lock"]:
        holds = MEDIA.setdefault("holds", {})
        for k in [k for k, until in holds.items() if until <= now]:
            del holds[k]
        return set(MEDIA.get("waiters") or {}) | set(holds)


def hold_media(clip: str) -> None:
    """A monitor was told `clip`'s preview is still being made (a 503 or a status that
    says building) and now waits on it while the board plays past the shot: for HOLD_S
    (the board asks again well inside it) that build is waited on — the yield thread
    leaves it running while the Program plays, and the walk builds it before the rest.
    Before verdict 8 the 20 s held request itself was the waiter."""
    with MEDIA["lock"]:
        MEDIA.setdefault("holds", {})[str(preview_path(Path(clip).stem))] = (
            time.monotonic() + HOLD_S)


def preview_eta(clip: str) -> float | None:
    """Seconds until `clip`'s preview is likely there: its build's estimate (footage
    length × the measured build rate, PREVIEW_RATE until one is measured) less how long
    it has run; not started yet, plus the sooner of the two builds holding the slots."""
    stem = Path(clip).stem
    key = str(preview_path(stem))
    if preview_path(stem).exists():
        return 0.0
    dur = clip_duration(clip) or 60.0
    rate = MEDIA.get("preview_rate") or PREVIEW_RATE[bool(MEDIA.get("gpu"))]
    est = 1.0 + dur * rate
    now = time.monotonic()
    with MEDIA["lock"]:
        started = dict(MEDIA.get("started") or {})
    if key in started:
        t0, e = started[key]
        return round(max(1.0, e - (now - t0)), 1)
    if len(started) >= 2:
        est += min(max(0.0, e - (now - t0)) for t0, e in started.values())
    return round(est, 1)


SLOT_WAIT = "*preview-slot*"     # an asked-for preview waits for PREVIEW_SLOTS


class _Held:
    """Release an already-acquired semaphore at the end of a `with` block."""

    def __init__(self, sem) -> None:
        self.sem = sem

    def __enter__(self):
        return self.sem

    def __exit__(self, *exc) -> None:
        self.sem.release()


def poster_scope() -> procscope.Scope:
    """The scope a poster the board asked for builds in: not background (never paused,
    never niced), but its ffmpeg dies with the server. Fresh after a shutdown's kill."""
    with MEDIA["lock"]:
        sc = MEDIA.get("poster_scope")
        if sc is None or sc.cancelled:
            sc = MEDIA["poster_scope"] = procscope.Scope("posters")
        return sc


def playing_now() -> bool:
    now = time.monotonic()
    with PLAYING["lock"]:
        tabs = PLAYING["tabs"]
        for k in [k for k, until in tabs.items() if until <= now]:
            del tabs[k]
        return bool(tabs)


def _signal_pid(pid: int, sig: int) -> bool:
    try:
        os.kill(pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def yield_tick() -> dict:
    """One look: while the Program plays, pause every background ffmpeg nobody waits on;
    otherwise (and for a build somebody now waits on) let the paused ones run again.
    Every background ffmpeg, a close look's own included, is at nice 19."""
    play = playing_now()
    procs = procscope.background_procs()
    waited = _waited_on()
    slot = SLOT_WAIT in waited       # every media build (tagged) runs until the slot frees
    want = {pid for pid, tag in procs
            if play and tag not in waited and not (slot and tag is not None)}
    stopped: dict = PLAYING["stopped"]
    for pid, _tag in procs:
        try:
            if os.getpriority(os.PRIO_PROCESS, pid) < procscope.BACKGROUND_NICE:
                os.setpriority(os.PRIO_PROCESS, pid, procscope.BACKGROUND_NICE)
        except OSError:
            pass
    for pid in want - set(stopped):
        if _signal_pid(pid, signal.SIGSTOP):
            stopped[pid] = time.monotonic()
    for pid in set(stopped) - want:
        _signal_pid(pid, signal.SIGCONT)
        stopped.pop(pid, None)
    return {"playing": play, "stopped": sorted(stopped), "background": [p for p, _ in procs]}


def _yield_loop() -> None:
    while True:
        try:
            yield_tick()
        except Exception as exc:  # noqa: BLE001 — the loop must outlive one bad look
            print(f"  !! yield: {exc}", flush=True)
        time.sleep(YIELD_TICK_S)


def start_yield() -> None:
    with PLAYING["lock"]:
        t = PLAYING["thread"]
        if t is not None and t.is_alive():
            return
        t = PLAYING["thread"] = threading.Thread(target=_yield_loop, daemon=True,
                                                 name="yield")
    t.start()


@app.post("/api/playing")
async def api_playing(request: Request) -> JSONResponse:
    """The board's Program started or stopped playing (`{on, tab}`). `on: true` again
    every couple of seconds while it plays is the heartbeat."""
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    on = bool(body.get("on"))
    tab = str(body.get("tab") or "board")[:64]
    with PLAYING["lock"]:
        if on:
            PLAYING["tabs"][tab] = time.monotonic() + PLAY_LAPSE_S
        else:
            PLAYING["tabs"].pop(tab, None)
        PLAYING["log"].append((time.time(), tab, on))     # the last beats, for a look
        del PLAYING["log"][:-50]
    start_yield()
    tick = await asyncio.to_thread(yield_tick)     # act now, not on the next tick
    return JSONResponse({"playing": tick["playing"], "paused_jobs": len(tick["stopped"])})


def _shutdown_children() -> None:
    """The server is going away: every encode it started goes with it (RANGEPROXY's
    orphans: encodes that kept the GPU busy after the server died)."""
    with PLAYING["lock"]:
        PLAYING["tabs"].clear()
    for pid in list(PLAYING["stopped"]):
        _signal_pid(pid, signal.SIGCONT)
    PLAYING["stopped"].clear()
    for proc in list(RENDER_PROCS.values()):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    procscope.kill_all()


app.router.on_shutdown.append(_shutdown_children)


def clip_for_stem(stem: str) -> str | None:
    """The bin's clip file for a URL's stem, or None. Only names that are in the
    footage folder resolve, so a URL can never reach anything else on disk."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", stem or ""):
        return None
    for clip in footage_clips():
        if Path(clip).stem == stem:
            return clip
    return None


def want_media(stem: str) -> None:
    """The human opened this clip: it goes to the front of both queues."""
    with MEDIA["lock"]:
        wanted = MEDIA["wanted"]
        if stem in wanted:
            wanted.remove(stem)
        wanted.insert(0, stem)
        del wanted[64:]


def first_wanted(pending: list[str]) -> str:
    """The pending clip the human asked for most recently, else the first the cut plays
    (`cut_clips`), else the first in order."""
    with MEDIA["lock"]:
        wanted = list(MEDIA["wanted"])
    by_stem = {Path(c).stem: c for c in pending}
    for stem in wanted:
        if stem in by_stem:
            return by_stem[stem]
    for clip in cut_clips():
        if Path(clip).stem in by_stem:
            return by_stem[Path(clip).stem]
    return pending[0]


def cut_clips() -> list[str]:
    """The clips the cut plays, each once, in the order the Program meets them: V1's
    shots, then V2+ and A3+, then a proposal nobody has answered yet (the first pass's
    cut before its Accept). Their proxies and previews are built before the rest of the
    bin's (verdict 5 punch 2: the Program showed the soft 360p preview for the first
    ~90 minutes of a Killington project, the bin's proxies going in priority order)."""
    out: list[str] = []
    for _key, s in _cut_uses():
        c = s.get("clip")
        if isinstance(c, str) and c and c not in out:
            out.append(c)
    return out


def _cut_uses() -> list[tuple[str, dict]]:
    """Every use of a clip the cut plays, as (where, shot) in the order the Program meets
    them: V1's shots ("segments"), V2+ ("overlays"), A3+ ("audio"), then the shots of a
    proposal nobody has answered yet ("proposal")."""
    out: list[tuple[str, dict]] = []
    try:
        edl = read_edl()
    except Exception:  # noqa: BLE001 — no cut yet is an empty list, not an error
        edl = {}
    for key in ("segments", "overlays", "audio"):
        for s in edl.get(key) or []:
            if isinstance(s, dict):
                out.append((key, s))
    try:
        recs = _ask_records(newest=1)
    except Exception:  # noqa: BLE001
        recs = []
    for rec in recs:
        if not rec.get("answered"):
            for s in (rec.get("plan") or {}).get("segments") or []:
                if isinstance(s, dict):
                    out.append(("proposal", s))
    return out


def _cut_proxy_next(j: journal.Journal) -> str | None:
    """A clip the cut plays whose proxy stage could start now (the journal's own rules:
    the proxy pool has a free worker, the stage is queued or retryable and out of its
    backoff, the probe is settled, the clip is neither parked nor missing), or None."""
    kind = journal.KIND["proxy"]
    if sum(1 for _c, s in j.running() if journal.KIND[s] == kind) >= int(j.workers.get(kind, 1)):
        return None
    now = time.time()
    for clip in cut_clips():
        rec = j.clips.get(clip)
        if not rec or rec.get("missing") or rec.get("parked"):
            continue
        st = rec["stages"]["proxy"]
        if st["state"] not in journal.RETRYABLE:
            continue
        if st.get("not_before") and float(st["not_before"]) > now:
            continue
        if any(rec["stages"][d]["state"] not in journal.SETTLED for d in journal.DEPENDS["proxy"]):
            continue
        return clip
    return None


def _rate(s: str | None) -> float | None:
    try:
        num, _, den = str(s or "").partition("/")
        v = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def media_facts(src: Path) -> dict | None:
    """What a browser needs to know about a file: codecs, profile, size, rotation, fps,
    bitrate; and what the Project panel shows of it: the camera's source timecode (the
    `timecode` tag on the picture, a timecode track, or the container) and the sound's
    channels and sample rate. One ffprobe per file version, cached by path, size and
    mtime (so a second bin with the same stem, or a replaced file, is probed afresh)."""
    try:
        st = src.stat()
    except OSError:
        return None
    key = (str(src), st.st_size, st.st_mtime_ns)
    with MEDIA["lock"]:
        if key in MEDIA["facts"]:
            return MEDIA["facts"][key]
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,profile,pix_fmt,width,height,avg_frame_rate,"
         "r_frame_rate,channels,sample_rate:stream_tags=timecode:stream_side_data=rotation"
         ":format=format_name,bit_rate:format_tags=timecode",
         "-of", "json", str(src)], capture_output=True, text=True)
    facts = None
    if r.returncode == 0:
        try:
            info = json.loads(r.stdout or "{}")
        except ValueError:
            info = {}
        streams = info.get("streams") or []
        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if v:
            rot = 0
            for sd in v.get("side_data_list") or []:
                if "rotation" in sd:
                    rot = int(float(sd["rotation"]))
            fmt = info.get("format") or {}
            try:
                bps = int(fmt.get("bit_rate") or 0)
            except ValueError:
                bps = 0
            # the picture's own tag first, then a timecode track's (tmcd), then the file's
            tc = next((str((x.get("tags") or {}).get("timecode") or "")
                       for x in [v] + streams if (x.get("tags") or {}).get("timecode")),
                      str((fmt.get("tags") or {}).get("timecode") or ""))
            try:
                arate = int(a.get("sample_rate") or 0) if a else 0
            except ValueError:
                arate = 0
            facts = {"vcodec": v.get("codec_name"), "profile": str(v.get("profile") or ""),
                     "pix_fmt": str(v.get("pix_fmt") or ""),
                     "width": int(v.get("width") or 0), "height": int(v.get("height") or 0),
                     "rotation": rot,
                     "fps": _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate")),
                     "acodec": a.get("codec_name") if a else None, "bit_rate": bps,
                     "timecode": tc or None,
                     "channels": int(a.get("channels") or 0) or None if a else None,
                     "sample_rate": arate or None}
    with MEDIA["lock"]:
        MEDIA["facts"][key] = facts
    return facts


def source_normalise(src: Path) -> str | None:
    """build_proxy's HDR tone map for this file (None for SDR), probed once per file
    version: every poster of an original would otherwise pay an ffprobe."""
    try:
        st = src.stat()
    except OSError:
        return None
    key = ("normalise", str(src), st.st_size, st.st_mtime_ns)
    with MEDIA["lock"]:
        if key in MEDIA["facts"]:
            return MEDIA["facts"][key]
    try:
        norm = colourmod.normalise_vf(colourmod.probe(src))
    except RuntimeError:
        norm = None
    with MEDIA["lock"]:
        MEDIA["facts"][key] = norm
    return norm


def browser_playable(src: Path) -> bool:
    """Whether a <video> can play this file as it is, and should. Conservative: a
    codec every Chrome decodes, 8-bit 4:2:0, no larger than 1080p and no fatter than
    25 Mb/s (it streams to a laptop over a tunnel), and no rotation tag the project
    has said to ignore (`orient: none` — the browser would honour it and play it
    sideways)."""
    f = media_facts(src)
    if not f:
        return False
    ext = src.suffix.lower()
    if f["vcodec"] not in BROWSER_VIDEO.get(ext, ()):
        return False
    if f["acodec"] and f["acodec"] not in BROWSER_AUDIO.get(ext, ()):
        return False
    if f["pix_fmt"] not in ("yuv420p", "yuvj420p"):
        return False
    if any(k in f["profile"] for k in ("10", "4:2:2", "4:4:4")):
        return False
    if max(f["width"], f["height"]) > PLAYABLE_MAX_EDGE or f["bit_rate"] > PLAYABLE_MAX_BPS:
        return False
    # The browser turns a tagged original itself, exactly as the export's autorotate does
    # (`orient: auto`), but only by quarter turns; `orient: none` says the tag is wrong
    # and the browser would honour it anyway. Either way: a preview, built by the rule.
    if f["rotation"] % 360 and (STATE.get("orient") == "none" or f["rotation"] % 90):
        return False
    return True


# The picture cache's version, in every preview's and poster's file name. v1 previews of
# rotation-flagged clips were made on the all-GPU path, which does not apply the flag, and
# posters were cut from them: upside down next to an upright export (verdict 3 punch 1).
# Bump it whenever what a preview or poster shows changes, so an old --work rebuilds.
PICTURE_V = "v2"


def poster_url(stem: str, t: float | None = None) -> str:
    """A poster's URL as the server hands it out: `<stem>.<PICTURE_V>.jpg` (and `?t=` when
    given). Posters are served `immutable` for a year, so a browser that cached a pre-v2
    (upside down) frame keeps it until the URL changes: the version is in the URL as well
    as in the file name (lead's wave-4 queue). In the name, not a query, so every page
    that appends `?t=` to a clip's poster keeps working; /media/poster drops it."""
    url = f"/media/poster/{stem}.{PICTURE_V}.jpg"
    return url if t is None else f"{url}?t={t:.2f}"


def preview_dir() -> Path:
    """Per bin, under --work, like the proxies and posters: disposable."""
    return STATE["work"] / "previews" / STATE["footage"].name


def preview_path(stem: str) -> Path:
    return preview_dir() / f"{stem}.{PICTURE_V}.mp4"


def preview_url(stem: str) -> str:
    return f"/media/preview/{preview_path(stem).name}"


def turn_needed(f: dict, orient: str) -> int:
    """The quarter-turns a clip's display matrix asks for, as the counter-clockwise
    degrees ffprobe reports (0, 90, 180, 270) — 0 under `orient: none`, which overrides
    the matrix with `-display_rotation 0` everywhere (proxy, poster, export)."""
    if orient == "none":
        return 0
    return int(round(float(f.get("rotation") or 0))) % 360


# The filter that does what ffmpeg's autorotate does for a matrix of that many
# counter-clockwise degrees (ffmpeg's own mapping; tested against it by pixels).
TURN_VF = {90: "transpose=cclock", 180: "hflip,vflip", 270: "transpose=clock"}


def preview_gpu() -> bool:
    """Whether ffmpeg can encode on an NVIDIA GPU here (one tiny probe encode, cached).
    `ROUGHCUT_PREVIEW_GPU=0` turns the GPU paths off — the software path's test."""
    if os.environ.get("ROUGHCUT_PREVIEW_GPU") == "0":
        return False
    if MEDIA["gpu"] is None:
        try:
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi",
                 "-i", "testsrc2=size=256x144:rate=10:duration=0.3",
                 "-c:v", "h264_nvenc", "-f", "null", "-"],
                capture_output=True, text=True, timeout=30)
            MEDIA["gpu"] = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            MEDIA["gpu"] = False
    return bool(MEDIA["gpu"])


def build_preview(src: Path, dest: Path, orient: str) -> str:
    """A 360p, keyframe-every-half-second h264/aac mp4 of `src` at `dest`; returns the
    path it took. Fastest first, each falling through on failure: all on the GPU
    (NVDEC + scale_cuda + NVENC; SDR 8-bit, no rotation to apply), the same with the
    frames brought down after the scale for a CPU quarter-turn (a rotation-flagged SDR
    clip: Karl's GoPro bins, flagged −180°), GPU decode with the CPU filters an HDR clip
    needs, then all software. Temp name and rename, as build_proxy: the server serves
    while this runs.

    The orient rule is build_proxy's and the export's: under `orient: auto` the display
    matrix is applied to the pixels and none is left on the file; under `orient: none`
    it is overridden to 0 and the stored pixels play as they are. ffmpeg's autorotate
    does the first on the CPU paths; on CUDA frames it silently does nothing, so the
    all-GPU path is taken only for a clip that needs no turn (verdict 3 punch 1: 9 of
    12 Killington clips played upside down in every monitor), and the GPU-turn path
    overrides the matrix and turns explicitly with TURN_VF."""
    f = media_facts(src) or {}
    norm = source_normalise(src)
    fps = f.get("fps") or 30.0
    gop = max(1, round(fps * PREVIEW_GOP_S))
    w, h = f.get("width") or 0, f.get("height") or 0
    turn = turn_needed(f, orient)
    rotated = turn % 180 != 0                  # a quarter turn swaps the edges
    if rotated:
        w, h = h, w
    # the short edge to PREVIEW_H, never up; the other edge follows, even
    if w >= h:
        tw, th = -2, min(PREVIEW_H, h or PREVIEW_H) // 2 * 2
    else:
        tw, th = min(PREVIEW_H, w or PREVIEW_H) // 2 * 2, -2
    pre = ["-display_rotation", "0"] if orient == "none" else []
    io = ["-map", "0:v:0", "-map", "0:a:0?", "-dn", "-sn"]
    tail = ["-g", str(gop), "-bf", "0", "-c:a", "aac", "-b:a", "96k", "-ac", "2",
            "-movflags", "+faststart", "-f", "mp4"]
    nvenc = ["-c:v", "h264_nvenc", "-preset", "p1", "-b:v", "1500k", "-maxrate", "3M",
             "-bufsize", "3M"]
    cpu_vf = f"scale={tw}:{th},format=yuv420p"
    if norm:
        cpu_vf = f"{norm},{cpu_vf}"
    paths: list[tuple[str, list[str]]] = []
    if preview_gpu():
        eight_bit = f.get("pix_fmt") in ("yuv420p", "yuvj420p", "nv12")
        cuda = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i", str(src), *io]
        if not norm and eight_bit and not turn:
            paths.append(("gpu", [*pre, *cuda, "-vf", f"scale_cuda={tw}:{th}", *nvenc]))
        elif not norm and eight_bit and turn in TURN_VF:
            # scaled on the GPU in stored orientation (edges swapped back for a quarter
            # turn), then a 360p frame comes down for the turn: the 5.3K decode and the
            # scale stay on the card. The matrix is overridden so none is left on the
            # file for the browser to apply a second time.
            sw, sh = (th, tw) if rotated else (tw, th)
            paths.append(("gpu-turn", [
                "-display_rotation", "0", *cuda, "-vf",
                f"scale_cuda={sw}:{sh},hwdownload,format=nv12,{TURN_VF[turn]}", *nvenc]))
        paths.append(("gpu-decode", [*pre, "-hwaccel", "cuda", "-i", str(src), *io,
                                     "-vf", cpu_vf, *nvenc]))
    paths.append(("cpu", [*pre, "-i", str(src), *io, "-vf", cpu_vf, "-c:v", "libx264",
                          "-preset", "ultrafast", "-crf", "30"]))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(f".{os.getpid()}.{threading.get_ident()}.part.mp4")
    err = ""
    for name, args in paths:
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", *args, *tail,
                            str(tmp)], capture_output=True, text=True)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size:
            tmp.replace(dest)
            return name
        err = r.stderr[-300:]
        tmp.unlink(missing_ok=True)
    raise RuntimeError(f"preview failed for {src.name}: {err}")


def needs_preview(clip: str) -> bool:
    stem = Path(clip).stem
    if (STATE["proxy_dir"] / f"{stem}.mp4").exists() or preview_path(stem).exists():
        return False
    src = clip_path(clip)
    return src.exists() and not browser_playable(src)


PREVIEW_RETRY_S = 120.0         # a failed preview is tried again after this long


def _claim_preview(key: str) -> tuple[threading.Event, bool]:
    """The build event for a preview path, and whether the caller now owns the build."""
    with MEDIA["lock"]:
        ev = MEDIA["building"].get(key)
        if ev is not None:
            return ev, False
        ev = MEDIA["building"][key] = threading.Event()
        return ev, True


def _build_claimed(src: Path, dest: Path, orient: str, ev: threading.Event,
                   asked: bool = False) -> None:
    """Paths fixed by the caller, not read from STATE here: a board re-pointed at
    another bin mid-build must not have this write that bin's preview from this one."""
    key = str(dest)
    scope = None
    gen = MEDIA["walk_gen"]
    try:
        # An asked-for preview waiting for a slot the walk's paused builds hold: they run
        # again until it has one (a Program playing must not starve the Source monitor).
        with (media_waiting(SLOT_WAIT) if asked else nullcontext()):
            PREVIEW_SLOTS.acquire()
        with _Held(PREVIEW_SLOTS):
            # a Stop while it waited for the slot: it does not start after the Stop (the
            # walk no longer waits on a build another thread owns, so it can be queued here)
            if MEDIA["walk_gen"] != gen:
                print(f"  preview {dest.stem} stopped", flush=True)
            elif not dest.exists():
                t0 = time.monotonic()
                # what preview_eta reads: when this build started and what it should take
                dur = clip_duration(src.name) if clip_path(src.name) == src else None
                rate = MEDIA.get("preview_rate") or PREVIEW_RATE[bool(MEDIA.get("gpu"))]
                with MEDIA["lock"]:
                    MEDIA.setdefault("started", {})[key] = (t0, 1.0 + (dur or 60.0) * rate)
                scope = media_scope(asked)
                try:
                    with procscope.active(scope), procscope.tagged(key):
                        how = build_preview(src, dest, orient)
                finally:
                    with MEDIA["lock"]:
                        MEDIA["started"].pop(key, None)
                took = time.monotonic() - t0
                if dur and dur > 5:      # the measured rate, smoothed, for the next estimate
                    MEDIA["preview_rate"] = round(0.6 * rate + 0.4 * (took / dur), 4)
                print(f"  preview {dest.stem} ({how}) in {took:.1f}s", flush=True)
        MEDIA["failed"].pop(key, None)
    except (RuntimeError, OSError) as exc:
        if scope is not None and scope.cancelled:
            # stopped, not broken: the clip's status stays "not built", never "failed"
            MEDIA["failed"].pop(key, None)
            print(f"  preview {dest.stem} stopped", flush=True)
        else:
            MEDIA["failed"][key] = (time.monotonic(), str(exc)[:300])
            print(f"  !! {exc}", flush=True)
    finally:
        with MEDIA["lock"]:
            MEDIA["building"].pop(key, None)
        ev.set()


def _failed_recently(key: str) -> bool:
    f = MEDIA["failed"].get(key)
    return bool(f) and time.monotonic() - f[0] < PREVIEW_RETRY_S


def ensure_preview(clip: str) -> Path | None:
    """Build `clip`'s preview unless it is there or being built (then wait for that
    build). Returns the preview, or None when it failed or the clip is gone."""
    dest = preview_path(Path(clip).stem)
    if dest.exists():
        return dest
    if not clip_path(clip).exists():
        return None
    ev, owner = _claim_preview(str(dest))
    if owner:
        _build_claimed(clip_path(clip), dest, STATE.get("orient", "auto"), ev)
    else:
        with media_waiting(str(dest)):
            ev.wait()
    return dest if dest.exists() else None


def start_preview(clip: str) -> threading.Event | None:
    """Make `clip`'s preview now, in its own thread (an asked-for clip does not queue
    behind the background walk). The event is set when the build ends; None when the
    last attempt failed within PREVIEW_RETRY_S — the status says so instead of the
    board retrying a broken file in a loop."""
    dest = preview_path(Path(clip).stem)
    if dest.exists():
        done = threading.Event()
        done.set()
        return done
    key = str(dest)
    if _failed_recently(key) or not clip_path(clip).exists():
        return None
    ev, owner = _claim_preview(key)
    if owner:
        threading.Thread(target=_build_claimed, daemon=True, args=(
            clip_path(clip), dest, STATE.get("orient", "auto"), ev, True)).start()
    return ev


def media_state(clip: str) -> str | None:
    """The bin's word on a clip's picture (verdict 8: it said "indexed" while the Program
    sat black on the clip's still-building preview), without probing anything:
    "proxy" / "range" / "preview" / "original" when something plays, "building" while
    its preview is being made or a monitor waits on it, "queued" when the walk (GPU box)
    will make it, "failed", or None when not known yet (no facts cached, nothing asked)."""
    stem = Path(clip).stem
    if (STATE["proxy_dir"] / f"{stem}.mp4").exists():
        return "proxy"
    key = str(preview_path(stem))
    if preview_path(stem).exists():
        return "range" if range_record(stem) is not None else "preview"
    with MEDIA["lock"]:
        building = key in MEDIA["building"]
    if building or key in _waited_on():
        return "building"
    if _failed_recently(key):
        return "failed"
    src = clip_path(clip)        # wave 12 IMPORT: an import's own path
    try:
        st = src.stat()
    except OSError:
        return None
    with MEDIA["lock"]:
        known = (str(src), st.st_size, st.st_mtime_ns) in MEDIA["facts"]
    if not known:
        return None
    if browser_playable(src):
        return "original"
    walk = bool(MEDIA.get("gpu")) and STATE.get("proxies_enabled", True)
    return "queued" if walk else None


def media_fallback(clip: str, wait_s: float = 0.0) -> tuple[str, str | None]:
    """What a <video> should play for `clip` right now: ("proxy", None), ("original",
    url), ("preview", url) — or ("building", None) / ("failed", None) after waiting up
    to `wait_s` for a preview that is being made."""
    stem = Path(clip).stem
    if (STATE["proxy_dir"] / f"{stem}.mp4").exists():
        return "proxy", None
    if browser_playable(clip_path(clip)):
        return "original", f"/media/original/{stem}.mp4"
    rec = range_record(stem)
    if rec is not None:
        return "range", rec["url"]
    if preview_path(stem).exists():
        return "preview", preview_url(stem)
    ev = start_preview(clip)
    if ev is None:
        return "failed", None
    if wait_s > 0:
        with media_waiting(str(preview_path(stem))):   # a monitor holds this request
            ev.wait(wait_s)
    if (STATE["proxy_dir"] / f"{stem}.mp4").exists():
        return "proxy", None
    if preview_path(stem).exists():
        return "preview", preview_url(stem)
    return "building", None


# ---------------------------------------------------------------- range proxies
#
# Wave 7 (PLAYBACK's measurement): on Killington the cut's 9 clips took ~37 min to get
# their 1280 proxies with one worker (1.53 s of encode per second of 59.94 HEVC, the whole
# of every clip decoded on the CPU), and the Program showed the soft 360p preview all that
# time. A cut plays a few seconds of each clip. So before the whole-clip proxies, every
# clip the cut plays gets a RANGE PROXY: a 1280 file on the clip's own timeline (it starts
# at 0 and lasts the clip, so every clip-time seek the player makes lands where it would in
# the proxy) whose frames inside the cut's ranges — each shot's [in − pad, out + pad] —
# are decoded from the original, and whose frames outside them are the 360p preview's,
# scaled up (no worse than what played before). Only the ranges touch the 4K master.
#
#   * Profile: PROXY_W wide (capped, as the proxy), the proxy's rate rule (59.94 → 29.97,
#     `proxy_rate`), the orient rule (the display matrix applied under `orient: auto`,
#     overridden under `none`), a keyframe every PROXY_GOP_S. On a GPU everything stays
#     on the card (NVDEC → scale_cuda → overlay_cuda → NVENC; a turn is done on the
#     1280 frame on the CPU); elsewhere, or for an HDR / 10-bit source, the CPU path
#     with the proxy's own filters (and NVENC when there is a GPU, else x264 veryfast).
#   * Timestamps: the preview's own (the clip's), and each range's frames moved back to
#     theirs after the input seek (`setpts=PTS+round(a/TB)`, whole ticks: setpts
#     truncates a float sum, and one tick short puts every 59.94 frame that sits half-way
#     between two 29.97 slots into the earlier one, a source frame off), then both
#     through the same `fps` filter as the proxy, so a range frame at clip time t is the
#     proxy's frame at t (the 59.94 → 29.97 phase included).
#   * Cache: `<proxy_dir>/ranges/<stem>.<key>.mp4` + a `.json` written after it, the key a
#     hash of (RANGE_V, PROXY_V, PICTURE_V, orient, the source's size and mtime, the
#     ranges). The newest current one for a clip is what `/media/proxy` 307s to
#     (`x-roughcut-media: range`), ahead of the preview; the whole-clip proxy, when it
#     lands, is served before it and the range files go RANGE_KEEP_S later.
#   * A trim that takes a shot past its range: the file still plays (the preview's frames
#     there), and the walk builds a new one for the cut as it now is.
#
# Built by the media walk (GPU boxes, like the previews): the cut's clips in film order,
# each one's preview then its range proxy, then the rest of the bin's previews; the walk
# then stays and looks at the cut every RANGE_POLL_S. `ROUGHCUT_RANGE_PROXIES=0` turns it
# off (the before-measurement).

RANGE_V = "r1"
RANGE_PAD_S = 1.0        # each shot's range reaches this far past its in and out points
RANGE_LEAD_S = 0.35      # a shot is covered if its range starts this far before the in
RANGE_TX_LEAD_S = 0.35   # a transition's incoming follower parks this far before its half (app.js TX_PRE, 0.25)
RANGE_MERGE_S = 2.0      # two ranges closer than this are encoded as one
RANGE_MAX = 8            # ranges per encode (each is an open decoder); closest merge first
RANGE_POLL_S = 2.0       # how often the walk looks at the cut once it is idle
RANGE_SETTLE_S = 1.5     # a cut saved this recently is still being edited: wait
RANGE_KEEP_S = 300.0     # range files outlive their clip's whole proxy this long
RANGE_NAME = re.compile(r"^(?P<stem>[A-Za-z0-9._-]+)\.(?P<key>[0-9a-f]{12})\.mp4$")


def ranges_enabled() -> bool:
    return os.environ.get("ROUGHCUT_RANGE_PROXIES", "1") != "0"


def range_dir() -> Path:
    return STATE["proxy_dir"] / "ranges"


def transition_handles(edl: dict | None = None) -> dict[str, tuple[float, float]]:
    """Per V1 shot id, the clip seconds a transition at its edges plays beyond it in the
    Program (verdict 7 punch 4): `(head, tail)` — the incoming shot's media before its in
    (the half, plus RANGE_TX_LEAD_S where app.js parks and starts its follower), the
    outgoing shot's past its out (the half). Each at the shot's speed; the asked half (the
    render may shorten it to the media; more is harmless, merge_ranges clamps to the clip);
    a cut with a dissolve and a crossfade takes the longer, as the Program's window does."""
    if edl is None:
        try:
            edl = read_edl()
        except Exception:  # noqa: BLE001 — no cut yet: no transitions
            return {}
    segs = [s for s in edl.get("segments") or [] if isinstance(s, dict)]
    order = {s.get("id"): k for k, s in enumerate(segs) if s.get("id")}
    out: dict[str, list[float]] = {}
    for t in edl.get("transitions") or []:
        if not isinstance(t, dict):
            continue
        k = order.get(t.get("a"))
        if k is None or k + 1 >= len(segs) or segs[k + 1].get("id") != t.get("b"):
            continue
        try:
            half = float(t.get("dur")) / 2
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(half) and half > 0):
            continue
        a, b = segs[k], segs[k + 1]
        ha = out.setdefault(a["id"], [0.0, 0.0])
        hb = out.setdefault(b["id"], [0.0, 0.0])
        ha[1] = max(ha[1], half * tracksmod.speed(a))
        hb[0] = max(hb[0], (half + RANGE_TX_LEAD_S) * tracksmod.speed(b))
    return {k: (round(v[0], 3), round(v[1], 3)) for k, v in out.items()}


def cut_ranges() -> dict[str, list[tuple[float, float]]]:
    """The clip-time ranges the Program plays, per clip of the bin, clips in the order the
    cut meets them (`_cut_uses`, V1 then V2+ then a waiting proposal; A3+ is sound only).
    A V1 shot with a transition at an edge reaches over its handle there
    (`transition_handles`), so the follower that plays it is in the range proxy too; the
    cache key hashes these ranges (range_key), so a transition added or made longer than
    the file holds is a new key, and the walk builds it (ranges_cover says the old one no
    longer covers); one removed or shortened is still covered, and the file stays."""
    out: dict[str, list[tuple[float, float]]] = {}
    handles = transition_handles()
    for key, s in _cut_uses():
        if key == "audio":
            continue
        clip = s.get("clip")
        try:
            a, b = float(s.get("in")), float(s.get("out"))
        except (TypeError, ValueError):
            continue
        if not isinstance(clip, str) or not clip or not (math.isfinite(a) and math.isfinite(b)):
            continue
        if b > a >= 0:
            head, tail = handles.get(s.get("id"), (0.0, 0.0)) if key == "segments" else (0.0, 0.0)
            if head or tail:
                a, b = round(max(0.0, a - head), 3), round(b + tail, 3)
            out.setdefault(clip, []).append((a, b))
    return out


def merge_ranges(spans: list[tuple[float, float]], dur: float | None) -> list[tuple[float, float]]:
    """The ranges to encode for these shots: each padded by RANGE_PAD_S, clamped to the
    clip, merged where they overlap or come within RANGE_MERGE_S, at most RANGE_MAX (the
    closest pairs merge first). Rounded to the millisecond, so the cache key is stable."""
    end = float(dur) if dur and dur > 0 else math.inf
    pad = sorted((max(0.0, a - RANGE_PAD_S), min(end, b + RANGE_PAD_S)) for a, b in spans)
    out: list[list[float]] = []
    for a, b in pad:
        if b <= a:
            continue
        if out and a - out[-1][1] < RANGE_MERGE_S:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    while len(out) > RANGE_MAX:
        i = min(range(len(out) - 1), key=lambda k: out[k + 1][0] - out[k][1])
        out[i][1] = max(out[i][1], out.pop(i + 1)[1])
    return [(round(a, 3), round(b, 3)) for a, b in out]


def ranges_cover(have, want, dur: float | None) -> bool:
    """Whether the encoded ranges `have` hold every shot in `want` sharp: from
    RANGE_LEAD_S before its in-point (where the Program parks the buffer) to its out."""
    end = float(dur) if dur and dur > 0 else math.inf
    for a, b in want:
        lo, hi = max(0.0, a - RANGE_LEAD_S), min(end, b)
        if not any(x <= lo + 1e-3 and y >= hi - 1e-3 for x, y in have or []):
            return False
    return True


def _range_src_id(src: Path) -> list[int] | None:
    try:
        st = src.stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


def range_key(src: Path, spans, orient: str) -> str:
    import hashlib
    blob = json.dumps([RANGE_V, PROXY_V, PICTURE_V, orient, _range_src_id(src),
                       [[round(a, 3), round(b, 3)] for a, b in spans]])
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def range_record(stem: str) -> dict | None:
    """The newest current range proxy of a clip: its sidecar, with `path` and `url`, or
    None. Current = this RANGE_V / PROXY_V / PICTURE_V, this project's orient, this
    version of the source file, the file there."""
    if not ranges_enabled():
        return None
    d = STATE.get("proxy_dir")
    clip = clip_for_stem(stem)
    if d is None or clip is None or not (d / "ranges").is_dir():
        return None
    src_id = _range_src_id(clip_path(clip))
    best = None
    for js in (d / "ranges").glob(f"{stem}.*.json"):
        try:
            rec = json.loads(js.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("stem") != stem:
            continue
        if (rec.get("v"), rec.get("proxy_v"), rec.get("picture_v")) != (RANGE_V, PROXY_V, PICTURE_V):
            continue
        if rec.get("orient") != STATE.get("orient", "auto") or rec.get("src") != src_id:
            continue
        mp4 = js.with_suffix(".mp4")
        if not mp4.exists():
            continue
        if best is None or float(rec.get("made") or 0) > float(best.get("made") or 0):
            best = {**rec, "path": mp4, "url": f"/media/range/{mp4.name}"}
    return best


def _range_rate(src: Path) -> str:
    """The frame rate of a range proxy, as the proxy's: `proxy_rate`, else the source's."""
    from fractions import Fraction
    rate = proxy_rate(src)
    if rate:
        return rate
    fps = (media_facts(src) or {}).get("fps") or 30.0
    r = Fraction(fps).limit_denominator(1001)
    return f"{r.numerator}/{r.denominator}"


def build_range_proxy(src: Path, preview: Path, dest: Path, spans, orient: str) -> str:
    """The range proxy of `src` at `dest` (see the section's comment): the preview's frames
    scaled to the proxy's size, with the frames of each of `spans` decoded from `src` laid
    over them at their own times. Returns the path it took ("gpu" or "cpu"). Temp name and
    rename, as build_proxy."""
    f = media_facts(src)
    if not f or not f.get("width") or not f.get("height"):
        raise RuntimeError(f"range proxy: cannot read {src.name}")
    if not spans:
        raise RuntimeError(f"range proxy: no ranges for {src.name}")
    turn = turn_needed(f, orient)
    quarter = turn % 180 != 0
    ow, oh = (f["height"], f["width"]) if quarter else (f["width"], f["height"])
    w = min(PROXY_W, ow) // 2 * 2
    h = max(2, round(oh * w / ow / 2) * 2)
    rate = _range_rate(src)
    num, _, den = rate.partition("/")
    gop = max(1, round(float(num) / float(den or 1) * PROXY_GOP_S))
    norm = source_normalise(src)
    gpu = preview_gpu()
    nvenc = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0",
             "-g", str(gop), "-bf", "0"]
    x264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(PROXY_CRF),
            "-force_key_frames", f"expr:gte(t,n_forced*{PROXY_GOP_S:g})"]
    tail = ["-map", "0:a:0?", "-c:a", "copy", "-dn", "-sn",
            "-write_tmcd", "0", "-movflags", "+faststart", "-f", "mp4"]

    def cut(a: float, b: float) -> list[str]:
        return ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(src)]

    def chain(base: str, over: str, label: str, cuda: bool) -> str:
        ov = "overlay_cuda" if cuda else "overlay"
        return f"[{base}][{over}]{ov}=eof_action=pass:repeatlast=0[{label}]"

    paths: list[tuple[str, list[str]]] = []
    eight_bit = f.get("pix_fmt") in ("yuv420p", "yuvj420p", "nv12")
    if gpu and not norm and eight_bit and (turn == 0 or turn in TURN_VF):
        hw = ["-hwaccel", "cuda", "-hwaccel_device", "rc", "-hwaccel_output_format", "cuda"]
        args = ["-init_hw_device", "cuda=rc", "-filter_hw_device", "rc", *hw, "-i", str(preview)]
        fc = [f"[0:v]fps={rate},scale_cuda={w}:{h}:format=nv12[b0]"]
        sw, sh = (h, w) if quarter else (w, h)
        turn_vf = (f",hwdownload,format=nv12,{TURN_VF[turn]},hwupload_cuda" if turn else "")
        for i, (a, b) in enumerate(spans, 1):
            # The matrix is overridden on every range input and the turn done explicitly:
            # autorotate does nothing to CUDA frames (ORIENT's lesson).
            args += ["-display_rotation", "0", *hw, *cut(a, b)]
            fc.append(f"[{i}:v]scale_cuda={sw}:{sh}:format=nv12{turn_vf},"
                      f"setpts=PTS+round({a:.3f}/TB),fps={rate}[s{i}]")
            fc.append(chain(f"b{i - 1}", f"s{i}", f"b{i}", True))
        fc[-1] = fc[-1].rsplit("[", 1)[0] + "[v]"
        paths.append(("gpu", [*args, "-filter_complex", ";".join(fc), "-map", "[v]",
                              *nvenc]))
    pre = ["-display_rotation", "0"] if orient == "none" else []
    dec = ["-hwaccel", "cuda"] if gpu else []
    args = ["-i", str(preview)]
    fc = [f"[0:v]fps={rate},scale={w}:{h},format=yuv420p[b0]"]
    for i, (a, b) in enumerate(spans, 1):
        args += [*pre, *dec, *cut(a, b)]
        vf = f"scale={w}:{h},format=yuv420p,setpts=PTS+round({a:.3f}/TB),fps={rate}"
        if norm:
            vf = f"{norm},{vf}"
        fc.append(f"[{i}:v]{vf}[s{i}]")
        fc.append(chain(f"b{i - 1}", f"s{i}", f"b{i}", False))
    fc[-1] = fc[-1].rsplit("[", 1)[0] + "[v]"
    paths.append(("cpu", [*args, "-filter_complex", ";".join(fc), "-map", "[v]",
                          *(nvenc if gpu else x264)]))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(f".{os.getpid()}.{threading.get_ident()}.part.mp4")
    err = ""
    for name, args in paths:
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", *args, *tail, str(tmp)],
                           capture_output=True, text=True)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size:
            tmp.replace(dest)
            return name
        err = r.stderr[-300:]
        tmp.unlink(missing_ok=True)
    raise RuntimeError(f"range proxy failed for {src.name}: {err}")


def range_wanted(clip: str, cut: dict | None = None) -> list[tuple[float, float]]:
    return list((cut_ranges() if cut is None else cut).get(clip) or [])


def needs_range(clip: str, cut: dict | None = None) -> bool:
    """Whether the walk should (re)build `clip`'s range proxy now: the cut plays it, it has
    neither a whole proxy nor a playable original, its preview exists (the frames between
    the ranges), and the newest range proxy does not hold every shot of it sharp."""
    if not ranges_enabled():
        return False
    want = range_wanted(clip, cut)
    stem = Path(clip).stem
    src = clip_path(clip)
    if not want or clip_for_stem(stem) != clip or not src.exists():
        return False
    if (STATE["proxy_dir"] / f"{stem}.mp4").exists() or browser_playable(src):
        return False
    if not preview_path(stem).exists():
        return False
    dur = clip_duration(clip)
    rec = range_record(stem)
    if rec is not None and ranges_cover(rec.get("spans"), want, dur):
        return False
    key = range_key(src, merge_ranges(want, dur), STATE.get("orient", "auto"))
    return not _failed_recently(str(range_dir() / f"{stem}.{key}.mp4"))


def ensure_range(clip: str, cut: dict | None = None) -> dict | None:
    """Build `clip`'s range proxy for the cut as it is now, unless the newest one already
    covers it. Returns the record served afterwards (None when there is nothing to build
    or the build failed)."""
    want = range_wanted(clip, cut)
    stem = Path(clip).stem
    src = clip_path(clip)
    if not want or not src.exists():
        return None
    dur = clip_duration(clip)
    rec = range_record(stem)
    if rec is not None and ranges_cover(rec.get("spans"), want, dur):
        return rec
    preview = ensure_preview(clip)
    if preview is None:
        return rec
    orient = STATE.get("orient", "auto")
    spans = merge_ranges(want, dur)
    rdir = range_dir()
    dest = rdir / f"{stem}.{range_key(src, spans, orient)}.mp4"
    key = str(dest)
    if _failed_recently(key):
        return rec
    ev, owner = _claim_preview(key)
    if not owner:
        ev.wait()
        return range_record(stem)
    scope = None
    try:
        with PREVIEW_SLOTS:
            t0 = time.monotonic()
            scope = media_scope()
            with procscope.active(scope), procscope.tagged(key):
                how = build_range_proxy(src, preview, dest, spans, orient)
            took = time.monotonic() - t0
            meta = {"stem": stem, "clip": clip, "v": RANGE_V, "proxy_v": PROXY_V,
                    "picture_v": PICTURE_V, "orient": orient, "src": _range_src_id(src),
                    "spans": [list(s) for s in spans], "how": how,
                    "built_s": round(took, 2), "made": time.time()}
            tmpj = dest.with_suffix(f".{os.getpid()}.part.json")
            tmpj.write_text(json.dumps(meta), encoding="utf-8")
            tmpj.replace(dest.with_suffix(".json"))
            sharp = sum(b - a for a, b in spans)
            print(f"  range proxy {stem} ({how}, {len(spans)} ranges, {sharp:.0f} s sharp) "
                  f"in {took:.1f}s", flush=True)
        MEDIA["failed"].pop(key, None)
        prune_ranges(stem)
    except (RuntimeError, OSError) as exc:
        if scope is not None and scope.cancelled:
            MEDIA["failed"].pop(key, None)
            print(f"  range proxy {stem} stopped", flush=True)
        else:
            MEDIA["failed"][key] = (time.monotonic(), str(exc)[:300])
            print(f"  !! {exc}", flush=True)
    finally:
        with MEDIA["lock"]:
            MEDIA["building"].pop(key, None)
        ev.set()
    return range_record(stem)


def prune_ranges(stem: str | None = None) -> int:
    """Range files nobody will be served again: all but the two newest of a clip (a
    player may still hold the one before), and every one of a clip whose whole proxy has
    been there RANGE_KEEP_S. Returns how many files went."""
    rdir = range_dir()
    if not rdir.is_dir():
        return 0
    by_stem: dict[str, list[Path]] = {}
    for mp4 in rdir.glob("*.mp4"):
        m = RANGE_NAME.match(mp4.name)
        if m and (stem is None or m["stem"] == stem):
            by_stem.setdefault(m["stem"], []).append(mp4)
    gone = 0
    now = time.time()
    for s, files in by_stem.items():
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        proxy = STATE["proxy_dir"] / f"{s}.mp4"
        old = (proxy.exists() and now - proxy.stat().st_mtime > RANGE_KEEP_S)
        for p in files if old else files[2:]:
            p.unlink(missing_ok=True)
            p.with_suffix(".json").unlink(missing_ok=True)
            gone += 1
    return gone


def _next_media_job() -> tuple[str, str] | None:
    """The walk's next piece of work after the posters, skipping a preview another
    thread is building (an asked-for one: the walk would only sit waiting on it):

      1. a preview a monitor is waiting on now (`hold_media`: the Program played onto a
         shot with no picture), cut clips among those in film order;
      2. the preview of every clip the cut plays, in film order (verdict 8: after the
         Accept, every shot of the cut has a picture before any of them is made sharp —
         the range proxies used to go between them, ~13 s each on Killington, so the
         ninth clip's preview waited behind eight range builds);
      3. then each cut clip's range proxy, in film order;
      4. then every other clip's preview (`first_wanted` order: one the human opened
         first). None when idle."""
    def failed(c: str) -> bool:
        return _failed_recently(str(preview_path(Path(c).stem)))
    with MEDIA["lock"]:
        busy = set(MEDIA["building"])
    todo = [c for c in footage_clips() if needs_preview(c) and not failed(c)
            and str(preview_path(Path(c).stem)) not in busy]
    held = _waited_on()
    cut_order = list(cut_ranges()) if ranges_enabled() else cut_clips()
    rank = {c: i for i, c in enumerate(cut_order)}
    waiting = [c for c in todo if str(preview_path(Path(c).stem)) in held]
    if waiting:
        return "preview", min(waiting, key=lambda c: rank.get(c, len(rank)))
    for clip in cut_order:
        if clip in todo:
            return "preview", clip
    if ranges_enabled():
        cut = cut_ranges()
        settled = True
        try:
            settled = time.time() - STATE["edl"].stat().st_mtime >= RANGE_SETTLE_S
        except (OSError, KeyError, TypeError):
            pass
        if settled:
            for clip in cut:
                if needs_range(clip, cut):
                    return "range", clip
    if todo:
        return "preview", first_wanted(todo)
    return None


def kick_previews() -> None:
    """Start the background walk: a poster at 0 s for every clip, then a preview for
    every clip that needs one, asked-for clips first. Only while proxies are enabled
    (tests and --no-proxies build nothing behind the human's back), and previews only
    on a GPU: on the CPU they would take the pass's cores, so there they are built on
    demand only."""
    if not STATE.get("proxies_enabled", True):
        return
    with MEDIA["lock"]:
        t = MEDIA["thread"]
        if t is not None and t.is_alive() and MEDIA.get("walk_bin") == STATE["footage"]:
            return
        t = MEDIA["thread"] = threading.Thread(target=_media_walk, daemon=True)
        MEDIA["walk_bin"] = STATE["footage"]
    t.start()


def _media_walk() -> None:
    from concurrent.futures import ThreadPoolExecutor
    try:
        footage = STATE["footage"]
        gen = MEDIA["walk_gen"]

        def poster(clip: str) -> None:
            if STATE["footage"] != footage or MEDIA["walk_gen"] != gen:
                return                      # re-pointed at another bin, or stopped
            try:
                clip_duration(clip)         # cached: the card frame is 1 s in
                with procscope.active(media_scope()):
                    ensure_poster(Path(clip).stem, poster_time(clip),
                                  slots=WALK_POSTER_SLOTS)
            except RuntimeError as exc:
                print(f"  !! {exc}", flush=True)

        with ThreadPoolExecutor(2) as pool:
            list(pool.map(poster, footage_clips()))
        if not preview_gpu():
            return
        # Previews, the cut's first with each one's range proxy after it; then, with
        # range proxies on, the walk stays and looks at the cut every RANGE_POLL_S (a
        # trim past a range, a new proposal) until the bin changes, a Stop, or proxies
        # are turned off.
        while (STATE["footage"] == footage and MEDIA["walk_gen"] == gen
               and STATE.get("proxies_enabled", True)):
            job = _next_media_job()
            if job is None:
                if not ranges_enabled():
                    return
                prune_ranges()
                MEDIA["kick"].wait(RANGE_POLL_S)
                MEDIA["kick"].clear()
                continue
            kind, clip = job
            if kind == "preview":
                ensure_preview(clip)
            else:
                ensure_range(clip)
    finally:
        with MEDIA["lock"]:
            if MEDIA["thread"] is threading.current_thread():
                MEDIA["thread"] = None


MEDIA_MIME = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4",
              ".webm": "video/webm"}


@app.get("/media/original/{name}")
def media_original(name: str, request: Request) -> Response:
    """A footage original, for a browser that can play it as it is (and only then:
    a 4K HEVC master is never handed to a <video>)."""
    clip = clip_for_stem(Path(name).stem)
    src = clip_path(clip) if clip else None
    if src is None or not browser_playable(src):
        raise HTTPException(404, f"no playable original: {Path(name).name}")
    return ranged_file(src, request, mime=MEDIA_MIME.get(src.suffix.lower()))


@app.get("/media/preview/{name}")
def media_preview(name: str, request: Request) -> Response:
    """A clip's 360p preview — what plays until its 720p proxy exists. Only the current
    PICTURE_V's file: an older one may be upside down, and a page still holding its
    URL gets a 404 and asks /media/proxy again rather than playing it."""
    name = Path(name).name
    tail = f".{PICTURE_V}.mp4"
    stem = name[:-len(tail)] if name.endswith(tail) else ""
    if not stem or name != preview_path(stem).name:
        raise HTTPException(404, f"not found: {name}")
    return ranged_file(preview_path(stem), request)


@app.get("/media/status/{name}")
def media_status(name: str) -> JSONResponse:
    """What `/media/proxy/<name>` would play now, without waiting — for a board that got
    a 503 and wants to know when to try again. Asking counts as wanting the clip: its
    preview starts (or keeps going) and its proxy is next."""
    stem = Path(name).stem
    clip = clip_for_stem(stem)
    if clip is None:
        raise HTTPException(404, f"no such clip: {Path(name).name}")
    want_media(stem)
    kind, url = media_fallback(clip)
    key = str(preview_path(stem))
    rec = range_record(stem)
    want = range_wanted(clip)
    if kind == "building":
        hold_media(clip)            # asked about while it builds: a monitor waits on it
    return JSONResponse({
        "clip": clip, "serves": kind,
        # verdict 8: the board's wait line says how long ("preparing preview… CLIP_09 · ~N s")
        "eta_s": preview_eta(clip) if kind == "building" else None,
        "url": f"/media/proxy/{stem}.mp4" if kind == "proxy" else url,
        "ready": kind in ("proxy", "original", "range", "preview"),
        "proxy": (STATE["proxy_dir"] / f"{stem}.mp4").exists(),
        "preview": preview_path(stem).exists(),
        # The range proxy (wave 7): the clip-time ranges it holds sharp (1280, from the
        # original), and whether they hold every shot the cut plays of this clip now.
        "range": rec is not None,
        "ranges": [list(s) for s in rec.get("spans") or []] if rec else [],
        "cut_sharp": (kind in ("proxy", "original") or (
            kind == "range" and ranges_cover(rec.get("spans"), want, clip_duration(clip))))
        if want else None,
        "error": (MEDIA["failed"].get(key) or (0, None))[1] if kind == "failed" else None,
        "poster": f"/media/poster/{stem}.jpg"})


@app.get("/media/range/{name}")
def media_range(name: str, request: Request) -> Response:
    """A clip's range proxy (see "range proxies"): only a current one, by the name
    `/media/proxy` redirected to. A stale name 404s and the player asks /media/proxy
    again, as with an old preview."""
    m = RANGE_NAME.match(Path(name).name)
    if not m:
        raise HTTPException(404, f"not found: {Path(name).name}")
    path = range_dir() / m.group(0)
    if not path.with_suffix(".json").exists():
        raise HTTPException(404, f"not found: {path.name}")
    return ranged_file(path, request, mime="video/mp4")


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


SNAP_PAD_HEAD_S = 0.25      # a cut opens a beat before the sentence (the floor's rule)
SNAP_PAD_TAIL_S = 0.45      # and closes after the last word lands (transcript ends run early)
ONSET_MIN_GAP_S = 0.5


def onset_peaks(sidecar: dict, *, top: int = 120) -> list[float]:
    """The strongest audio onsets in a clip, as times — a local maximum above the track's
    mean + one standard deviation, at least half a second apart, the strongest `top`."""
    tracks = sidecar.get("tracks") or {}
    track = tracks.get("onset") or []
    hz = float(sidecar.get("frame_hz") or 10.0)
    if len(track) < 3 or hz <= 0:
        return []
    vals = [float(v) for v in track]
    mean = sum(vals) / len(vals)
    sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
    floor = mean + sd
    peaks = [(vals[i], i / hz) for i in range(1, len(vals) - 1)
             if vals[i] >= floor and vals[i] >= vals[i - 1] and vals[i] > vals[i + 1]]
    peaks.sort(reverse=True)
    out: list[float] = []
    for _v, t in peaks:
        if all(abs(t - u) >= ONSET_MIN_GAP_S for u in out):
            out.append(round(t, 2))
        if len(out) >= top:
            break
    return sorted(out)


def snap_points(clip: str) -> dict:
    """What a cut can snap to in this clip (INTAKE M9): sentence starts and ends — raw,
    and padded the way the floor and edl_snap cut them — word starts, and onset peaks.
    Free; from the audio sidecar alone. A clip without one snaps to nothing."""
    d = load_sidecar(clip)
    duration = clip_duration(clip) or 0.0
    sentences = []
    words = []
    for u in d.get("transcript") or []:
        try:
            a, b = float(u["start"]), float(u["end"])
        except (KeyError, TypeError, ValueError):
            continue
        sentences.append({"start": round(a, 2), "end": round(b, 2),
                          "cut_in": round(max(0.0, a - SNAP_PAD_HEAD_S), 2),
                          "cut_out": round(min(duration, b + SNAP_PAD_TAIL_S) if duration
                                           else b + SNAP_PAD_TAIL_S, 2),
                          "text": str(u.get("text", ""))[:80]})
        for w in u.get("words") or []:
            try:
                words.append(round(float(w["t"]), 2))
            except (KeyError, TypeError, ValueError):
                continue
    return {"clip": clip, "duration": duration, "sentences": sentences,
            "words": sorted(set(words)), "onsets": onset_peaks(d),
            "pads": {"head": SNAP_PAD_HEAD_S, "tail": SNAP_PAD_TAIL_S}}


@app.get("/api/snaps/{clip}")
def api_snaps(clip: str) -> JSONResponse:
    if clip not in footage_clips():
        raise HTTPException(404, f"unknown clip {clip!r}")
    return JSONResponse(snap_points(clip))


@app.get("/app.js")
def appjs() -> Response:
    return Response((HERE / "static" / "app.js").read_text(encoding="utf-8"),
                    media_type="application/javascript", headers=NO_STORE)


TIMELINE_FILES = {"timeline.js", "timeline.css", "timeline-trim.js", "timeline-keys.js",
                  "timeline-lanes.js"}


@app.get("/timeline/{name}")
def timeline_static(name: str) -> Response:
    """The promoted timeline's module and its lanes (INTAKE M9): one route, a fixed list —
    the board is served by name, never by directory listing."""
    if name not in TIMELINE_FILES:
        raise HTTPException(404, "no such timeline file")
    p = HERE / "static" / name
    if not p.exists():
        raise HTTPException(404, f"{name} is not built yet")
    media = "text/css" if name.endswith(".css") else "application/javascript"
    return Response(p.read_text(encoding="utf-8"), media_type=media, headers=NO_STORE)


# The workspace's own modules (redesign wave 2): one route and a fixed list, like the
# timeline's, so a lane adds a panel by adding a file here instead of a route each.
MODULE_FILES = {"firstpass.js", "firstpass.css", "export.js", "export.css", "meters.js",
                "meters.css", "effects.js", "effects.css", "workspace.css", "scopes.js",
                # wave 12 (K3): Import, actions (the AI's vocabulary + review), the AI prompt, the walk-through
                "import.js", "import.css", "actions.js", "actions.css", "aiprompt.js", "aiprompt.css",
                "tour.js", "tour.css"}


@app.get("/modules/{name}")
def module_static(name: str) -> Response:
    if name not in MODULE_FILES:
        raise HTTPException(404, "no such module")
    p = HERE / "static" / name
    if not p.exists():
        raise HTTPException(404, f"{name} is not built yet")
    media = "text/css" if name.endswith(".css") else "application/javascript"
    return Response(p.read_text(encoding="utf-8"), media_type=media, headers=NO_STORE)


# ---------------------------------------------------------------- audio peaks (wave 3 TLAUDIO)
#
# A1 draws each shot's real waveform (verdict 3, punch 6: "A1 has no waveform, just
# speech ticks"). The clip's sound is decoded once by ffmpeg to raw mono PCM, reduced to
# PEAKS_BASE min/max pairs a second, and cached under --work/audio/<bin>/ keyed by the
# original's size and mtime. A request asks for a coarser rate (`pps`, or `spp` samples
# per peak at PEAKS_SR) and gets the pairs for the whole clip as int8 (-127..127): the
# timeline slices a shot's range out of them itself, so a trim never asks again.
#
# Never inside the GET (wave 11 CHEAP; verdict 8: up to 10.4 s a request while an
# export ran): a background worker (`queue_peaks`, one decode at a time, nice 19 in a
# procscope) writes the file when the clip's sound is first heard (its sidecar lands,
# seen by the snapshot's watch) or first asked for, and keeps the pairs in memory. The
# GET answers from memory; until they exist, `202 {"pending": true}` at once, and the
# timeline asks again.

PEAKS_SR = 16000                 # Hz the PCM is decoded at (a waveform, not a master)
PEAKS_BASE = 100                 # pairs a second on disk
PEAKS_RATES = (10, 20, 25, 50, 100)
PEAKS_SLOTS = threading.BoundedSemaphore(2)
PEAKS_LOCKS: dict[str, threading.Lock] = {}
# a clip's sound rarely changes under its stem, but it can (a replaced file): an hour
# in the browser, not a year
PEAKS_CACHE = {"cache-control": "private, max-age=3600"}


def peaks_dir() -> Path:
    return STATE["work"] / "audio" / STATE["footage"].name


def peaks_source(stem: str) -> Path | None:
    """The file whose sound A1 shows: the proxy (the smallest file to demux), the 360p
    preview, else the original; a generated clip's own file."""
    if edits.is_generated(stem):
        gen = generated_dir() / f"{stem}.mp4"
        return gen if gen.exists() and (media_facts(gen) or {}).get("acodec") else None
    clip = clip_for_stem(stem)
    if clip is None:
        return None
    for p in (STATE["proxy_dir"] / f"{stem}.mp4", preview_path(stem), clip_path(clip)):
        if p.exists() and (media_facts(p) or {}).get("acodec"):
            return p
    return None


def peaks_dest(stem: str, src: Path | None = None) -> Path | None:
    """The peaks file for the clip's current version: keyed by the ORIGINAL's size and
    mtime when there is one (the proxy carries the same sound, so a proxy finishing
    never invalidates peaks made from the original), else by `src`'s (a generated
    clip's file). None when there is nothing to key on."""
    orig = clip_for_stem(stem)
    key_src = clip_path(orig) if orig and clip_path(orig).exists() else src
    if key_src is None:
        return None
    kst = key_src.stat()
    return peaks_dir() / f"{stem}.{kst.st_size}-{kst.st_mtime_ns}.peaks"


def peaks_built(stem: str):
    """The pairs from the clip's peaks file when it is already written (no ffmpeg, no
    ffprobe), else None."""
    import numpy as np

    gen = generated_dir() / f"{stem}.mp4" if edits.is_generated(stem) else None
    try:
        dest = peaks_dest(stem, gen if gen is not None and gen.exists() else None)
        if dest is not None and dest.exists():
            return np.frombuffer(dest.read_bytes(), dtype=np.int8).reshape(-1, 2)
    except OSError:
        pass
    return None


def build_peaks(stem: str):
    """The clip's base peaks (int8 array, shape (n, 2): min, max), from the cache or
    decoded now. None when the clip has no sound."""
    import numpy as np

    built = peaks_built(stem)
    if built is not None:
        return built
    src = peaks_source(stem)
    if src is None:
        return None
    dest = peaks_dest(stem, src)
    lock = PEAKS_LOCKS.setdefault(stem, threading.Lock())
    with lock:
        if dest.exists():
            return np.frombuffer(dest.read_bytes(), dtype=np.int8).reshape(-1, 2)
        with PEAKS_SLOTS:
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-i", str(src), "-vn", "-sn", "-dn",
                 "-ac", "1", "-ar", str(PEAKS_SR), "-f", "s16le", "-acodec", "pcm_s16le", "-"],
                capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(f"peaks failed for {stem}: "
                               f"{r.stderr[-200:].decode(errors='replace')}")
        pcm = np.frombuffer(r.stdout, dtype=np.int16).astype(np.int32)
        spp = PEAKS_SR // PEAKS_BASE
        n = -(-len(pcm) // spp)
        pad = np.zeros(n * spp, dtype=np.int32)
        pad[:len(pcm)] = pcm
        blocks = pad.reshape(n, spp)
        lo = np.clip(np.floor(blocks.min(axis=1) * 127 / 32768), -127, 127)
        hi = np.clip(np.ceil(blocks.max(axis=1) * 127 / 32767), -127, 127)
        out = np.stack([lo, hi], axis=1).astype(np.int8)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(f".{os.getpid()}.{threading.get_ident()}.part")
        tmp.write_bytes(out.tobytes())
        tmp.replace(dest)
        for old in dest.parent.glob(f"{stem}.*.peaks"):     # an earlier version of the file
            if old != dest:
                old.unlink(missing_ok=True)
        return out


# stem -> the base pairs (int8, (n, 2)), or None for a clip with no sound; filled by
# the worker only, read by the GET; cleared on a re-point and when the clip's file changes
PEAKS_MEM: dict = {}
PEAKS_JSON: dict[tuple[str, int], bytes] = {}      # (stem, rate) -> the answer's bytes
PEAKS_Q: dict = {"lock": threading.Lock(), "pending": [], "failed": {}, "thread": None,
                 "scope": None}
PEAKS_PENDING = {"cache-control": "no-store", "retry-after": "1"}


def queue_peaks(stems: list[str], front: bool = False) -> None:
    """Have the worker make (or read) these clips' peaks; `front`: somebody is waiting.
    Never blocks and never touches the disk: the worker checks the stems."""
    with PEAKS_Q["lock"]:
        pend = PEAKS_Q["pending"]
        for s in (reversed(stems) if front else stems):
            if s in PEAKS_MEM or s in PEAKS_Q["failed"]:
                continue
            if s in pend:
                if not front:
                    continue
                pend.remove(s)
            if front:
                pend.insert(0, s)
            else:
                pend.append(s)
        t = PEAKS_Q["thread"]
        if pend and (t is None or not t.is_alive()):
            t = PEAKS_Q["thread"] = threading.Thread(target=_peaks_loop, daemon=True,
                                                     name="peaks")
            t.start()


def _peaks_scope() -> procscope.Scope:
    with PEAKS_Q["lock"]:
        sc = PEAKS_Q["scope"]
        if sc is None or sc.cancelled:
            sc = PEAKS_Q["scope"] = procscope.Scope("peaks", background=True)
        return sc


def _peaks_loop() -> None:
    while True:
        with PEAKS_Q["lock"]:
            if not PEAKS_Q["pending"]:
                PEAKS_Q["thread"] = None
                return
            stem = PEAKS_Q["pending"][0]
        scope = _peaks_scope()
        try:
            if edits.is_generated(stem) or clip_for_stem(stem) is not None:
                with procscope.active(scope):
                    base = build_peaks(stem)
                if not scope.cancelled:
                    PEAKS_MEM[stem] = base
        except Exception as exc:  # noqa: BLE001 — one bad clip must not end the queue
            if not scope.cancelled:
                PEAKS_Q["failed"][stem] = str(exc)[:300]
                print(f"  !! peaks: {stem}: {exc}", flush=True)
        finally:
            with PEAKS_Q["lock"]:
                if stem in PEAKS_Q["pending"]:
                    PEAKS_Q["pending"].remove(stem)
                if scope.cancelled:              # the server is going away
                    PEAKS_Q["pending"].clear()
                    PEAKS_Q["thread"] = None
                    return


def forget_peaks(stem: str | None = None) -> None:
    """Drop a clip's peaks from memory (all of them: a re-point)."""
    with PEAKS_Q["lock"]:
        for d in (PEAKS_MEM, PEAKS_Q["failed"]):
            if stem is None:
                d.clear()
            else:
                d.pop(stem, None)
        for k in [k for k in PEAKS_JSON if stem is None or k[0] == stem]:
            PEAKS_JSON.pop(k, None)


def peaks_changed(changes: list[tuple[Path, str]]) -> None:
    """The snapshot's watch saw these names change: a clip's sidecar landing (its sound
    heard) queues its peaks; a clip's file replaced forgets them."""
    heard = []
    for d, name in changes:
        if not name:
            continue
        if str(d) == str(STATE.get("sidecars")) and name.endswith(".audio.json"):
            heard.append(name[: -len(".audio.json")])
        elif str(d) in (str(STATE.get("footage")), str(generated_dir()),
                        str(mediamod.uploads_dir(STATE["work"], STATE["footage"].name))):
            forget_peaks(Path(name).stem)
    if heard:
        queue_peaks(heard)


def _peaks_answer(stem: str, rate: int) -> bytes:
    import numpy as np

    got = PEAKS_JSON.get((stem, rate))
    if got is not None:
        return got
    base = PEAKS_MEM[stem]
    if base is None or not len(base):
        body = {"stem": stem, "pps": rate, "duration": 0.0, "min": [], "max": [],
                "silent": True}
    else:
        k = PEAKS_BASE // rate
        n = -(-len(base) // k)
        padded = np.zeros((n * k, 2), dtype=np.int8)
        padded[:len(base)] = base
        g = padded.reshape(n, k, 2)
        lo, hi = g[:, :, 0].min(axis=1), g[:, :, 1].max(axis=1)
        body = {"stem": stem, "pps": rate, "duration": round(len(base) / PEAKS_BASE, 3),
                "min": lo.astype(int).tolist(), "max": hi.astype(int).tolist()}
    out = json.dumps(body, separators=(",", ":")).encode("utf-8")
    PEAKS_JSON[(stem, rate)] = out
    return out


def _peaks_miss(stem: str) -> None:
    """A clip asked for whose peaks are not in memory: 404 when it is not one of the
    bin's; its file read now when it is already written (once per clip); else queued
    to the front."""
    if not edits.is_generated(stem) and clip_for_stem(stem) is None:
        raise HTTPException(404, f"no such clip: {stem}")
    with PEAKS_Q["lock"]:
        failed = PEAKS_Q["failed"].get(stem)
    if failed:
        raise HTTPException(500, failed)
    built = peaks_built(stem)
    if built is not None:
        PEAKS_MEM.setdefault(stem, built)
        return
    queue_peaks([stem], front=True)


@app.get("/api/peaks/{stem}")
async def api_peaks(stem: str, pps: int = 0, spp: int = 0) -> Response:
    """A clip's waveform: `pps` min/max pairs a second (10, 20, 25, 50 or 100; `spp`, the
    samples per peak at 16 kHz, is the other way to ask), int8 -127..127, for the whole
    clip: `{"stem", "pps", "duration", "min", "max"}`. A clip with no sound has empty
    lists and `"silent": true`. Until the clip's peaks are made (a background worker,
    never this request): `202 {"stem", "pps", "pending": true}`, uncached — ask again."""
    if re.search(r"\.(mp4|mov|mxf|mkv)$", stem, re.I):
        stem = Path(stem).stem
    if not re.fullmatch(r"[A-Za-z0-9._-]+", stem):
        raise HTTPException(404, f"no such clip: {stem}")
    want = pps if pps > 0 else (PEAKS_SR / spp if spp > 0 else 50)
    rate = min(PEAKS_RATES, key=lambda r: abs(r - want))
    if stem not in PEAKS_MEM:
        await asyncio.to_thread(_peaks_miss, stem)
    if stem not in PEAKS_MEM:
        return JSONResponse({"stem": stem, "pps": rate, "pending": True}, status_code=202,
                            headers=PEAKS_PENDING)
    if (stem, rate) in PEAKS_JSON:
        body = PEAKS_JSON[(stem, rate)]
    else:
        body = await asyncio.to_thread(_peaks_answer, stem, rate)
    return Response(body, media_type="application/json", headers=PEAKS_CACHE)



# ---------------------------------------------------------------- effects (INTAKE M12)
#
# AI-designed video + audio effects. Karl, 2026-09-20: *"Add call of duty hit markers
# where my skis are with the sound effect … AI then goes and adds separate overlaid
# video with the effect (which it also generates itself) and the audio. Human can
# iterate with the AI … but AI also tests / verifies that the DoD is complete."*
#
# The vocabulary, the model calls, the renderer and the checks are `roughcut.fx`. This
# is the loop around them, the same shape as Ask: a job designs a *proposal* into
# `work/fx/<bin>/<id>.json` (never the EDL); the human previews it on the monitor,
# iterates, verifies (a proof render of the one shot on the proxy, then the measured
# checklist), and Accept moves it into the EDL's `effects`, where the render reads it.

def fx_home() -> Path:
    return fx.fx_dir(STATE["work"], STATE["footage"].name)


def _fx_segments() -> list[dict]:
    return read_edl().get("segments") or []


def _fx_seg(shot: str) -> dict:
    if str(shot).startswith("new:"):
        return {"id": str(shot), "clip": "", "in": 0.0, "out": 0.0}    # a shot the edits will create
    seg = next((s for s in _fx_segments() if str(s.get("id")) == str(shot)), None)
    if seg is None:
        raise HTTPException(400, f"no shot {shot!r} in the cut")
    return seg


def _fx_proxy(clip: str) -> Path | None:
    """The clip's proxy — what the monitor plays and what a proof renders from — or
    the source when there is none yet."""
    p = STATE["proxy_dir"] / f"{Path(clip).stem}.mp4"
    if p.exists():
        return p
    if edits.is_generated(clip):                      # a slide the edits made (INTAKE M13)
        g = generated_dir() / clip
        if g.exists():
            return g
    src = clip_path(clip)
    return src if src.exists() else None


def _fx_anchor(e: dict) -> str | None:
    """The shot a proposal keyed to a shot the edits will create belongs to on the
    board, until Accept: the first op's shot, or the shot it is placed before / after."""
    if not str(e.get("shot", "")).startswith("new:"):
        return None
    for op in e.get("edits") or []:
        for k in ("shot", "before", "after", "from_shot"):
            v = op.get(k)
            if v and not str(v).startswith("new:"):
                return str(v)
    segs = _fx_segments()
    return str(segs[0]["id"]) if segs else None


def _film_clock(t: float) -> str:
    m, sec = divmod(int(round(max(0.0, t))), 60)
    return f"{m}:{sec:02d}"


def _film_starts(segments: list[dict]) -> dict[str, float]:
    out, t = {}, 0.0
    for sg in segments:
        out[str(sg.get("id"))] = t
        t += edits.dur(sg)
    return out


def _fx_edit_says(ops: list[dict], segments: list[dict]) -> str:
    """What a proposal's edits do to the film, in one or two plain sentences in film
    time (INTAKE M16 I16.5): "Slows 0.65 s to 0.4×. Shot 17 gets 1.0 s longer, at 2:50."
    — never a file name or a clip second ("CLIP_08.MP4 at 0.4× from 170.85 to 171.50s",
    the M13 words, read as clip time on a 3:09 film)."""
    try:
        clean = edits.validate_ops(ops, segments)
        after = edits.apply_ops(segments, None, clean)["segments"]
    except ValueError:
        return "This change no longer fits the cut."
    starts = _film_starts(segments)
    by_id = {str(sg.get("id")): sg for sg in segments}
    num = {str(sg.get("id")): i + 1 for i, sg in enumerate(segments)}

    def shot(sid) -> str:
        return f"shot {num[str(sid)]}" if str(sid) in num else "the new shot"

    def where(op: dict) -> str:
        if op.get("after") is not None:
            return f"after {shot(op['after'])}"
        if op.get("before") is not None:
            return f"before {shot(op['before'])}"
        return "at the end"

    def where_t(op: dict) -> float | None:
        if op.get("after") is not None and str(op["after"]) in by_id:
            return starts[str(op["after"])] + edits.dur(by_id[str(op["after"])])
        if op.get("before") is not None:
            return starts.get(str(op["before"]))
        return sum(edits.dur(sg) for sg in segments)

    def at_clip(sid, t) -> float | None:
        sg = by_id.get(str(sid))
        if sg is None:
            return None
        return starts[str(sid)] + (float(t) - float(sg["in"])) / edits.speed_of(sg)

    said, at = [], None
    for op in clean:
        k, sid = op["op"], op.get("shot")
        if k == "speed":
            verb = "Slows" if op["rate"] < 1 else "Speeds up"
            if "from" in op:
                said.append(f"{verb} {op['to'] - op['from']:.2f} s to {op['rate']:g}×")
                t = at_clip(sid, op["from"])
            else:
                said.append(f"{verb} {shot(sid)} to {op['rate']:g}×")
                t = starts.get(str(sid))
        elif k in ("extend", "set_range"):
            said.append(f"Re-trims {shot(sid)}")
            t = starts.get(str(sid))
        elif k == "split":
            said.append(f"Splits {shot(sid)}")
            t = at_clip(sid, op["at"])
        elif k == "freeze":
            said.append(f"Holds a frame of {shot(sid)} for {op['seconds']:g} s")
            t = at_clip(sid, op["at"])
        elif k == "generate":
            what = {"black": "black slide", "colour": "colour slide", "still": "still"}[op["kind"]]
            said.append(f"Adds a {op['seconds']:g} s {what} {where(op)}")
            t = where_t(op)
        elif k == "insert":
            said.append(f"Adds a {op['out'] - op['in']:.1f} s shot {where(op)}")
            t = where_t(op)
        elif k == "remove":
            said.append(f"Takes {shot(sid)} out")
            t = starts.get(str(sid))
        else:                                       # move
            said.append(f"Moves {shot(sid)} {where(op)}")
            t = None
        if at is None and t is not None:
            at = t
    first = said[0] if len(said) == 1 else (
        f"{said[0]} and {said[1][0].lower()}{said[1][1:]}" if len(said) == 2
        else f"{said[0]}, and {len(said) - 1} more changes")
    delta = sum(edits.dur(sg) for sg in after) - sum(edits.dur(sg) for sg in segments)
    one = {str(op.get("shot")) for op in clean} if all(op.get("shot") for op in clean) else set()
    if abs(delta) < 0.05:
        tail = ""
    elif len(one) == 1 and next(iter(one)) in num:
        tail = f" {shot(next(iter(one))).capitalize()} gets {abs(delta):.1f} s {'longer' if delta > 0 else 'shorter'}"
    else:
        tail = f" The film gets {abs(delta):.1f} s {'longer' if delta > 0 else 'shorter'}"
    if tail and at is not None:
        tail += f", at {_film_clock(at)}"
    return f"{first}.{tail}." if tail else f"{first}."


def _fx_film(e: dict, segments: list[dict]) -> dict | None:
    """Where the effect's shot sits in the film — after the proposal's own edits, so a
    shot they create has a place too: `{n, start, in, speed}`, the FX tool's way from
    clip seconds to film time when the board does not have the shot yet."""
    cut = segments
    if e.get("edits") and e.get("status") == "proposed":
        try:
            cut = edits.apply_ops(segments, None, edits.validate_ops(e["edits"], segments))["segments"]
        except ValueError:
            cut = segments
    starts = _film_starts(cut)
    for i, sg in enumerate(cut):
        if str(sg.get("id")) == str(e.get("shot")):
            return {"n": i + 1, "start": round(starts[str(sg["id"])], 3),
                    "in": float(sg["in"]), "speed": edits.speed_of(sg)}
    return None


def _fx_urls(e: dict, segments: list[dict] | None = None) -> dict:
    d = fx_home() / e["id"]
    out = dict(e)
    anchor = _fx_anchor(e)
    if anchor:
        out["anchor_shot"] = anchor
    segments = _fx_segments() if segments is None else segments
    if e.get("edits") and e.get("status") == "proposed":
        out["says"] = _fx_edit_says(e["edits"], segments)
    elif (e.get("applied") or {}).get("says"):
        out["says"] = e["applied"]["says"]
    film = _fx_film(e, segments)
    if film:
        out["film"] = film
    for key, name in (("sound_url", "sound.wav"), ("proof_url", "proof.mp4"),
                      ("base_url", "base.mp4"), ("strip_url", "strip.jpg"),
                      ("ref_url", "ref.png")):
        if (d / name).exists():
            out[key] = f"/api/fx/{e['id']}/{name}"
    return out


def _fx_all() -> list[dict]:
    edl = read_edl()
    accepted = list(edl.get("effects") or [])
    ids = {e.get("id") for e in accepted}
    proposed, revising = [], {}
    for p in sorted(fx_home().glob("fx_*.json")):
        try:
            e = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if e.get("id") in ids:
            # Change on an accepted effect (a revise) leaves the revision in the fx dir
            # under the same id, the accepted copy in the EDL: the revision is what
            # waits on Karl. It was never listed, so the paid-for change could not be
            # seen, previewed or accepted from the board (INTAKE M16 I16.5).
            if e.get("status") == "proposed":
                revising[e["id"]] = e
            continue
        if e.get("status") not in ("proposed", "removed"):
            continue
        proposed.append(e)
    # accepted first, then proposals, then what was removed (kept for Restore)
    proposed.sort(key=lambda e: e.get("status") == "removed")
    segments = edl.get("segments") or []
    return [_fx_urls(revising.get(e.get("id"), e), segments) for e in accepted + proposed]


def _fx_get(fx_id: str) -> dict:
    for e in read_edl().get("effects") or []:
        if e.get("id") == fx_id:
            return e
    e = fx.load(fx_home(), fx_id)
    if e is None:
        raise HTTPException(404, f"no effect {fx_id}")
    return e


def _fx_current(fx_id: str) -> dict:
    """The version on the board: a revision of an accepted effect waiting on Karl (its
    file in the fx dir, proposed) before the accepted copy in the EDL — what Change, a
    nudge and the free check work on."""
    e = fx.load(fx_home(), fx_id)
    if e is not None and e.get("status") == "proposed":
        return e
    return _fx_get(fx_id)


def _fx_put(e: dict) -> None:
    """Write an effect back where it lives: the EDL when accepted, the fx dir always
    (the dir is the home of its files either way)."""
    fx.save(fx_home(), e)
    if e.get("status") == "accepted":
        edl = read_edl()
        lst = list(edl.get("effects") or [])
        for i, x in enumerate(lst):
            if x.get("id") == e["id"]:
                lst[i] = e
                break
        else:
            lst.append(e)
        edl["effects"] = lst
        write_edl(edl)


def _fx_sound(e: dict) -> None:
    if e.get("sound"):
        d = fx_home() / e["id"]
        d.mkdir(parents=True, exist_ok=True)
        fx.synth_sound(e["sound"], d / "sound.wav")


def _fx_reference(fx_id: str, reference: dict | None) -> dict | None:
    """The human's drawing on a frame: the PNG to disk, the marks (fractions of the
    frame) and the goal text kept with the effect."""
    if not reference or not isinstance(reference, dict):
        return None
    d = fx_home() / fx_id
    d.mkdir(parents=True, exist_ok=True)
    png = reference.get("png") or ""
    if isinstance(png, str) and png.startswith("data:image/png;base64,"):
        try:
            (d / "ref.png").write_bytes(base64.b64decode(png.split(",", 1)[1]))
        except (ValueError, IndexError):
            pass
    marks = []
    for m in reference.get("marks") or []:
        try:
            x, y = float(m[0]), float(m[1])
        except (TypeError, ValueError, IndexError):
            continue
        marks.append([round(min(1, max(0, x)), 4), round(min(1, max(0, y)), 4)])
    out = {"goal": str(reference.get("goal") or "")[:400], "marks": marks[:fx.MAX_EVENTS]}
    try:
        out["t"] = round(float(reference.get("t")), 3)
    except (TypeError, ValueError):
        pass
    strokes = reference.get("strokes")
    if isinstance(strokes, list):
        out["strokes"] = strokes[:64]
    return out


def _fx_impact(e: dict) -> bool:
    words = ("hit", "impact", "crash", "rock", "strike", "bang", "land", "smack", "slam")
    text = f"{e.get('note', '')} {e.get('name', '')}".lower()
    return any(w in text for w in words)


def _fx_window(raw, seg: dict) -> tuple[float, float] | None:
    """The human's window on the shot, clip seconds, or None for the whole shot. A window
    that is not two numbers inside the shot, in order, is a 400."""
    if raw is None:
        return None
    try:
        t0, t1 = float(raw[0]), float(raw[1])
    except (TypeError, ValueError, IndexError):
        raise HTTPException(400, "window is not [from, to] in clip seconds")
    lo, hi = float(seg["in"]), float(seg["out"])
    if not (lo <= t0 < t1 <= hi + 1e-6):
        raise HTTPException(400, f"window {t0:.2f}–{t1:.2f} is not inside the shot {lo:.2f}–{hi:.2f}")
    return (round(t0, 3), round(min(t1, hi), 3))


def _fx_design_job(job: str, shot: str, note: str, place: bool, reference: dict | None,
                   window: tuple[float, float] | None = None) -> None:
    entry = FX[job]
    try:
        edl = read_edl()
        segments = edl.get("segments") or []
        seg = next(s for s in segments if str(s.get("id")) == str(shot))
        clips, _ = _ask_clips()
        clip = clips.get(seg["clip"]) or {"clip": seg["clip"]}
        fx_id = fx.new_id()
        ref = _fx_reference(fx_id, reference)
        entry.note("designing the effect" + (" from your drawing" if ref else ""))
        e = fx.design(note, seg, clip, load_sidecar(seg["clip"]), reference=ref,
                      place=bool(place), proxy=_fx_proxy(seg["clip"]) if place else None,
                      workdir=fx_home() / fx_id, segments=segments, clips=clips,
                      window=window)
        if window is not None:
            e["window"] = [window[0], window[1]]
        e["id"] = fx_id
        e["status"] = "proposed"
        if ref:
            e["reference"] = ref
        e = fx.validate_effect(e, segments, clips)
        fx.save(fx_home(), e)
        _fx_sound(e)
        entry["result"] = {"id": fx_id}
        _fx_autoverify(fx_id)                       # the free check, by itself (I16.5)
        n_ev, n_ed = len(e.get("events") or []), len(e.get("edits") or [])
        what = " · ".join(x for x in [f"{n_ev} moment{'s' if n_ev != 1 else ''}" if n_ev else "",
                                     f"{n_ed} change{'s' if n_ed != 1 else ''} to the cut" if n_ed else ""] if x)
        entry.finish("done", detail=f"{e['name']} — {what}, a proposal")
    except Exception as exc:  # noqa: BLE001 — the job reports, the server lives
        entry.finish("failed", detail=f"{type(exc).__name__}: {exc}"[:300])


def _fx_revise_job(job: str, fx_id: str, note: str) -> None:
    entry = FX[job]
    try:
        e = _fx_current(fx_id)
        segments = _fx_segments()
        clips, _ = _ask_clips()
        entry.note("revising the effect")
        new = fx.revise(e, note, segments, clips)
        new["id"] = fx_id
        new["status"] = "proposed"          # an iteration is a proposal again
        for k in ("reference",):
            if e.get(k) is not None:
                new[k] = e[k]
        new.pop("verify", None)
        new = fx.validate_effect(new, segments, clips)
        fx.save(fx_home(), new)
        _fx_sound(new)
        if e.get("status") == "accepted":
            # the accepted one stays in the EDL until this proposal is accepted over it
            pass
        entry["result"] = {"id": fx_id}
        _fx_autoverify(fx_id)
        entry.finish("done", detail=f"{new['name']} — revised, a proposal")
    except Exception as exc:  # noqa: BLE001
        entry.finish("failed", detail=f"{type(exc).__name__}: {exc}"[:300])


PROOF_W, PROOF_H, PROOF_FPS = 1280, 720, 24


def _fx_proof(e: dict, seg: dict) -> tuple[Path, Path]:
    """The one shot rendered from its proxy at 720p, with the effect (`proof.mp4`) and
    without (`base.mp4`), so the checks can measure a difference and the human can
    watch the result in seconds rather than after a whole render."""
    d = fx_home() / e["id"]
    d.mkdir(parents=True, exist_ok=True)
    src = _fx_proxy(seg["clip"])
    if src is None:
        raise RuntimeError(f"no proxy or source for {seg['clip']}")
    dur = float(seg["out"]) - float(seg["in"])
    vf = (f"scale={PROOF_W}:{PROOF_H}:force_original_aspect_ratio=decrease,"
          f"pad={PROOF_W}:{PROOF_H}:(ow-iw)/2:(oh-ih)/2,fps={PROOF_FPS},format=yuv420p")
    af = "aresample=48000:first_pts=0,aformat=channel_layouts=stereo"
    head = ["ffmpeg", "-v", "error", "-y", "-nostdin", "-ss", f"{float(seg['in']):.3f}",
            "-i", str(src)]
    tail = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart"]
    base, proof = d / "base.mp4", d / "proof.mp4"
    r = subprocess.run(head + ["-t", f"{dur:.3f}", "-vf", vf, "-af", af,
                               "-map", "0:v:0", "-map", "0:a:0?", "-dn"] + tail + [str(base)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"base render failed: {r.stderr[-300:]}")
    evs = fx.events_in_shot(e, seg)
    extra, graph, vout, aout = fx.part_graph([(e, evs)], PROOF_W, PROOF_H, PROOF_FPS, d)
    fc = f"[0:v]{vf}[vbase];[0:a]{af}[abase]" + (";" + graph if graph else "")
    # the extra inputs sit between the source and `-t`: after `-i` a `-t` is an input
    # option for the NEXT input, and the sprites must not be cut short
    r = subprocess.run(head + extra + ["-t", f"{dur:.3f}", "-filter_complex", fc,
                                       "-map", f"[{vout}]", "-map", f"[{aout}]", "-dn"]
                       + tail + [str(proof)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"proof render failed: {r.stderr[-300:]}")
    return proof, base


# The free check runs by itself (INTAKE M16 I16.5): after every design, revise, nudge
# and revert, never a model call — a proof render of the one shot and the measured
# checklist. Proposals arrived unchecked because the check waited for a Verify button
# (the slow motion on Killington never had one run). One proof renders at a time, after
# any render of the cut (a 4K encode owns the cores); a check already waiting for an
# effect covers a newer change too, because it reads the effect when it starts.
FX_VERIFY_SLOT = threading.Lock()
FX_VERIFY_GUARD = threading.Lock()
FX_VERIFY_WAITING: dict[str, str] = {}      # fx id → the job queued for it, not started
FX_VERIFY_POLL_S = 1.0
FX_SPEC_CHECKED = ("shot", "events", "overlay", "sound", "window", "edits")


def _fx_render_running() -> bool:
    return any(j["state"] not in progress.TERMINAL for j in RENDERS.values())


def _fx_autoverify(fx_id: str) -> str | None:
    """Queue the free check for an effect; returns its job id (the one already waiting
    when there is one), or None when the effect is gone."""
    with FX_VERIFY_GUARD:
        waiting = FX_VERIFY_WAITING.get(fx_id)
        if waiting and waiting in FX and FX[waiting]["state"] not in progress.TERMINAL:
            return waiting
        try:
            name = _fx_get(fx_id).get("name") or "the effect"
        except HTTPException:
            return None
        job = _fx_start("verify", f"Checking {name}", _fx_verify_job, fx_id, fx_id=fx_id)
        FX_VERIFY_WAITING[fx_id] = job
        return job


def _fx_verify_wait(entry: progress.Job, job: str, fx_id: str) -> None:
    """Behind any render, then behind another check; once started, a newer change to
    this effect queues a check of its own."""
    waited = False
    while _fx_render_running():
        if not waited:
            entry.note("waiting for the render to finish")
            waited = True
        time.sleep(FX_VERIFY_POLL_S)
    FX_VERIFY_SLOT.acquire()
    with FX_VERIFY_GUARD:
        if FX_VERIFY_WAITING.get(fx_id) == job:
            FX_VERIFY_WAITING.pop(fx_id)


def _fx_verify_job(job: str, fx_id: str) -> None:
    entry = FX[job]
    slot = False
    try:
        _fx_verify_wait(entry, job, fx_id)
        slot = True
        try:
            e = _fx_current(fx_id)
        except HTTPException:                       # discarded while it waited
            entry.finish("done", detail="the effect is gone")
            return
        seg = _fx_seg(e["shot"]) if not str(e.get("shot", "")).startswith("new:") else {"id": e["shot"], "clip": e.get("clip"), "in": 0, "out": 0}
        sc = load_sidecar(e["clip"])
        onset = (sc.get("tracks") or {}).get("onset") or None
        hz = float(sc.get("frame_hz") or fx.ONSET_HZ)
        edit_check = None
        if e.get("edits"):
            try:
                words = edits.validate_ops(e["edits"], _fx_segments(), _ask_clips()[0]) and \
                    edits.apply_ops(_fx_segments(), _ask_clips()[0], e["edits"])["words"]
                edit_check = {"key": "edits_apply", "label": "the edits fit the cut as it stands",
                              "ok": True, "detail": "; ".join(words)[:200]}
            except ValueError as exc:
                edit_check = {"key": "edits_apply", "label": "the edits fit the cut as it stands",
                              "ok": False, "detail": str(exc)[:200]}
        if e.get("overlay") is None and e.get("sound") is None:
            v = {"ok": bool(edit_check and edit_check["ok"]), "at": datetime.now().isoformat(timespec="seconds"),
                 "checks": [edit_check] if edit_check else []}
        elif str(e.get("shot", "")).startswith("new:"):
            v = {"ok": bool(edit_check and edit_check["ok"]),
                 "at": datetime.now().isoformat(timespec="seconds"),
                 "checks": ([edit_check] if edit_check else [])
                 + [{"key": "proof", "label": "a proof render", "ok": None,
                     "detail": "skipped: the shot does not exist until the edits are accepted"}]}
        else:
            entry.note("rendering a proof of the shot")
            proof, base = _fx_proof(e, seg)
            entry.note("measuring the proof")
            v = fx.verify(e, seg, onset=onset, hz=hz, part=proof, base=base, impact=_fx_impact(e))
            if edit_check:
                v["checks"].insert(0, edit_check)
                v["ok"] = bool(v["ok"]) and edit_check["ok"]
        # The effect may have changed while its proof rendered (a nudge, a revise, an
        # Accept that applied its edits): this checklist describes the old one, and the
        # change has queued its own check — writing this back would also undo the change.
        # Or it was discarded meanwhile (a check starts after every design, so a quick
        # Discard lands here): nothing to check, which is not a failure.
        try:
            fresh = _fx_current(fx_id)
        except HTTPException:
            entry["result"] = {"id": fx_id, "ok": None}
            entry.finish("done", detail="the effect is gone")
            return
        if {k: fresh.get(k) for k in FX_SPEC_CHECKED} != {k: e.get(k) for k in FX_SPEC_CHECKED}:
            entry["result"] = {"id": fx_id, "ok": None}
            entry.finish("done", detail="the effect changed meanwhile — checked again")
            return
        e = fresh
        e["verify"] = v
        _fx_put(e)
        entry["result"] = {"id": fx_id, "ok": bool(v.get("ok"))}
        failed = [c["label"] for c in v.get("checks", []) if c.get("ok") is False]
        entry.finish("done", detail="every check passed" if v.get("ok")
                     else "failed: " + ", ".join(failed)[:200])
    except Exception as exc:  # noqa: BLE001
        entry.finish("failed", detail=f"{type(exc).__name__}: {exc}"[:300])
    finally:
        if slot:
            FX_VERIFY_SLOT.release()
        with FX_VERIFY_GUARD:
            if FX_VERIFY_WAITING.get(fx_id) == job:
                FX_VERIFY_WAITING.pop(fx_id)


def _fx_start(kind: str, label: str, target, *args, fx_id: str | None = None) -> str:
    job = uuid.uuid4().hex[:8]
    FX[job] = progress.Job("fx", label, id=job, state="running", fx_kind=kind,
                           **({"fx_id": fx_id} if fx_id else {}))
    threading.Thread(target=target, args=(job, *args), daemon=True).start()
    return job


def _fx_job(kind: str, label: str, target, *args) -> JSONResponse:
    return JSONResponse({"job": _fx_start(kind, label, target, *args)})


@app.get("/api/fx")
def api_fx_list() -> JSONResponse:
    return JSONResponse({"effects": _fx_all()})


def _fx_place_frames(seg: dict) -> int:
    """The frames a placing look reads: one per candidate impact in the shot."""
    sc = load_sidecar(seg["clip"])
    peaks = fx.onset_peaks((sc.get("tracks") or {}).get("onset") or [],
                           float(sc.get("frame_hz") or fx.ONSET_HZ),
                           float(seg["in"]), float(seg["out"]))
    return max(1, len(peaks))


@app.get("/api/fx/price")
def api_fx_price(place: int = 0, shot: str = "") -> JSONResponse:
    """The cost on the button before it is pressed: the design call, plus a frame per
    candidate impact when the model is asked to place the anchors by looking."""
    frames = _fx_place_frames(_fx_seg(shot)) if place and shot else 0
    return JSONResponse({"usd": fx.price(place_frames=frames), "frames": frames,
                         **config.cost_terms()})


@app.post("/api/fx/design")
async def api_fx_design(request: Request) -> JSONResponse:
    body = await request.json()
    shot = str(body.get("shot") or "")
    note = (body.get("note") or "").strip()
    if not note:
        raise HTTPException(400, "say what the effect is")
    seg = _fx_seg(shot)
    clips, _ = _ask_clips()
    if seg["clip"] not in clips:
        raise HTTPException(400, f"{seg['clip']} has no analysis yet")
    window = _fx_window(body.get("window"), seg)
    placing = bool(body.get("place")) and not body.get("reference")
    refuse_past_cap("the design", fx.price(place_frames=_fx_place_frames(seg) if placing else 0))
    return _fx_job("design", f"Designing an effect — {Path(seg['clip']).stem}",
                   _fx_design_job, shot, note, bool(body.get("place")), body.get("reference"),
                   window)


@app.post("/api/fx/revise")
async def api_fx_revise(request: Request) -> JSONResponse:
    body = await request.json()
    fx_id = str(body.get("id") or "")
    note = (body.get("note") or "").strip()
    if not note:
        raise HTTPException(400, "say what to change")
    e = _fx_get(fx_id)
    refuse_past_cap("the revision", fx.price(place_frames=0))
    return _fx_job("revise", f"Revising {e.get('name', 'the effect')}", _fx_revise_job, fx_id, note)


@app.post("/api/fx/verify")
async def api_fx_verify(request: Request) -> JSONResponse:
    body = await request.json()
    fx_id = str(body.get("id") or "")
    e = _fx_get(fx_id)
    _fx_seg(e["shot"])
    return JSONResponse({"job": _fx_autoverify(fx_id)})


FX_SPEC_KEYS = ("name", "why", "events", "overlay", "sound", "window")
MAX_PREVIOUS = 5


def _fx_snapshot(e: dict) -> dict:
    return {k: e.get(k) for k in FX_SPEC_KEYS if e.get(k) is not None}


@app.post("/api/fx/accept")
async def api_fx_accept(request: Request) -> JSONResponse:
    """Into the cut. An accepted version already there (this is a revised proposal
    over it) is kept under `previous`, so Accept is reversible: Revert brings it back.
    Karl, 2026-09-20: *"make sure that when effects are applied long term they are
    reversable / we can add more on top of them."*"""
    body = await request.json()
    fx_id = str(body.get("id") or "")
    e = fx.load(fx_home(), fx_id) or _fx_get(fx_id)
    undo = None
    # The edits first (INTAKE M13): applied to the cut in one write by the edits
    # section's apply_edits, the effect re-keyed from `new:n` to the minted id, and the
    # effect then validated against the cut as it now is.
    if e.get("edits") and e.get("status") != "accepted":
        applier = globals().get("apply_edits")
        if applier is None:
            raise HTTPException(501, "this build cannot apply edits to the cut yet")
        # what the board's Ctrl+Z of this accept takes back (POST /api/fx/undo): the
        # record as proposed and the EDL's effects as they were, before the re-keying
        undo = {"proposal": json.loads(json.dumps(e)),
                "effects_before": json.loads(json.dumps(read_edl().get("effects") or []))}
        says = _fx_edit_says(e["edits"], _fx_segments())     # the cut as Karl saw it
        try:
            applied = applier(e["edits"])
        except ValueError as exc:
            raise HTTPException(400, f"the edits no longer fit the cut: {exc}")
        id_map = applied.get("id_map") or {}
        if str(e.get("shot", "")).startswith("new:"):
            e["shot"] = id_map.get(e["shot"]) or e["shot"]
            new_seg = next((s for s in applied.get("segments") or [] if str(s.get("id")) == str(e["shot"])), None)
            if new_seg:
                e["clip"] = new_seg["clip"]
        e["applied"] = {"at": applied.get("at"), "before": applied.get("before"), "words": e.get("edit_words"),
                        "says": says}
        e.pop("edits", None)                        # applied: the cut carries them now
        if e.get("overlay") is None and e.get("sound") is None:
            # Edit-only (the slow motion on Killington): nothing is left to draw or hear,
            # so it is not an effect for the EDL. validate_effect refused it and Accept
            # answered 500 *after* changing the cut, with the proposal still waiting to
            # be accepted — and applied — again. It is kept on disk as the record, out
            # of every list; `applied.before` is what an undo takes back.
            e["status"] = "applied"
            fx.save(fx_home(), e)
            _fx_accept_record(fx_id, undo, e)
            return JSONResponse({"ok": True, "effect": _fx_urls(e)})
    segments = _fx_segments()
    current = next((x for x in read_edl().get("effects") or [] if x.get("id") == fx_id), None)
    if current is not None and _fx_snapshot(current) != _fx_snapshot(e):
        prev = list(current.get("previous") or [])
        prev.append({**_fx_snapshot(current), "at": current.get("verify", {}).get("at") or current.get("created")})
        e["previous"] = prev[-MAX_PREVIOUS:]
    elif current is not None and current.get("previous"):
        e["previous"] = current["previous"]
    e["status"] = "accepted"
    e = fx.validate_effect(e, segments)
    _fx_put(e)
    if undo is not None:
        _fx_accept_record(fx_id, undo, e)
    return JSONResponse({"ok": True, "effect": _fx_urls(e)})


# An accepted effect's edit to the cut is one entry on the board's undo stack (wave 12,
# AIOPS: roughcutRefresh({undo})). The cut on the board follows Ctrl+Z; these make the
# effect follow it too (wave 12, AIGLUE): its record reads proposed again (its edits
# waiting to be accepted) and the EDL's effects are as they were before the accept —
# the copies the re-keying made gone, the re-keyed ones back on their shot — and Ctrl+Shift+Z
# puts the accept back. Kept beside the effect's files, as `<id>/accept.json`.

def _fx_accept_path(fx_id: str) -> Path:
    if not re.fullmatch(r"fx_[A-Za-z0-9_-]{1,48}", fx_id or ""):
        raise HTTPException(404, f"no effect {fx_id!r}")
    return fx_home() / fx_id / "accept.json"


def _fx_accept_record(fx_id: str, undo: dict | None, accepted: dict) -> None:
    if undo is None:
        return
    path = _fx_accept_path(fx_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {**undo, "accepted": accepted, "effects_after": read_edl().get("effects") or [],
           "state": "done"}
    path.write_text(json.dumps(rec, indent=1), encoding="utf-8")


def _fx_patch(current: list[dict], frm: list[dict], to: list[dict]) -> list[dict]:
    """`current` with every effect that differs between `frm` and `to` made as in `to`
    (put back, replaced or taken out); the effects nobody touched in between stay."""
    f = {str(x.get("id")): x for x in frm}
    t = {str(x.get("id")): x for x in to}
    moved = {i for i in {*f, *t} if f.get(i) != t.get(i)}
    out = [t[str(x.get("id"))] if str(x.get("id")) in moved else x
           for x in current if str(x.get("id")) not in moved or str(x.get("id")) in t]
    have = {str(x.get("id")) for x in out}
    out.extend(x for x in to if str(x.get("id")) in moved and str(x.get("id")) not in have)
    return out


def _fx_follow(fx_id: str, back: bool) -> dict:
    path = _fx_accept_path(fx_id)
    if not path.exists():
        raise HTTPException(404, f"{fx_id} has no accepted edit to take back")
    rec = json.loads(path.read_text(encoding="utf-8"))
    want = "undone" if back else "done"
    if rec.get("state") == want:
        return {"ok": True, "state": want, "changed": False}
    edl = read_edl()
    frm, to = ((rec["effects_after"], rec["effects_before"]) if back
               else (rec["effects_before"], rec["effects_after"]))
    effects = _fx_patch(list(edl.get("effects") or []), frm, to)
    if effects:
        edl["effects"] = effects
    else:
        edl.pop("effects", None)
    write_edl(edl)
    fx.save(fx_home(), rec["proposal"] if back else rec["accepted"])
    rec["state"] = want
    path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return {"ok": True, "state": want, "changed": True}


@app.post("/api/fx/undo")
async def api_fx_undo(request: Request) -> JSONResponse:
    """The board's Ctrl+Z took an effect's edit out of the cut: the effect reads proposed
    again and the EDL's effects are as they were before its accept."""
    body = await request.json()
    return JSONResponse(_fx_follow(str(body.get("id") or ""), back=True))


@app.post("/api/fx/redo")
async def api_fx_redo(request: Request) -> JSONResponse:
    """Ctrl+Shift+Z put the edit back: the effect and the EDL's effects as accepted."""
    body = await request.json()
    return JSONResponse(_fx_follow(str(body.get("id") or ""), back=False))


@app.post("/api/fx/revert")
async def api_fx_revert(request: Request) -> JSONResponse:
    """Back to the version accepted before this one."""
    body = await request.json()
    e = _fx_get(str(body.get("id") or ""))
    prev = list(e.get("previous") or [])
    if not prev:
        raise HTTPException(400, "nothing to revert to")
    last = prev.pop()
    for k in FX_SPEC_KEYS:
        if k in last:
            e[k] = last[k]
        elif k in e and k not in ("name", "events", "overlay"):
            e.pop(k, None)
    e["previous"] = prev
    e.pop("verify", None)
    e = fx.validate_effect(e, _fx_segments())
    _fx_put(e)
    _fx_sound(e)
    _fx_autoverify(e["id"])
    return JSONResponse({"ok": True, "effect": _fx_urls(e)})


@app.post("/api/fx/restore")
async def api_fx_restore(request: Request) -> JSONResponse:
    """A removed effect back into the cut, as it was."""
    body = await request.json()
    fx_id = str(body.get("id") or "")
    e = fx.load(fx_home(), fx_id)
    if e is None or e.get("status") != "removed":
        raise HTTPException(404, f"no removed effect {fx_id}")
    e["status"] = "accepted"
    e = fx.validate_effect(e, _fx_segments())
    _fx_put(e)
    return JSONResponse({"ok": True, "effect": _fx_urls(e)})


@app.get("/api/fx/peaks")
def api_fx_peaks(shot: str = "") -> JSONResponse:
    """The onset peaks inside a shot — the candidate impacts the design starts from —
    so the FX tool can draw them on its range bar."""
    seg = _fx_seg(shot)
    sc = load_sidecar(seg["clip"])
    peaks = fx.onset_peaks((sc.get("tracks") or {}).get("onset") or [],
                           float(sc.get("frame_hz") or fx.ONSET_HZ),
                           float(seg["in"]), float(seg["out"]), top=fx.MAX_EVENTS)
    return JSONResponse({"peaks": peaks, "in": seg["in"], "out": seg["out"]})


@app.post("/api/fx/discard")
async def api_fx_discard(request: Request) -> JSONResponse:
    body = await request.json()
    fx_id = str(body.get("id") or "")
    e = fx.load(fx_home(), fx_id)
    if e is None:
        raise HTTPException(404, f"no proposal {fx_id}")
    current = next((x for x in read_edl().get("effects") or [] if x.get("id") == fx_id), None)
    if current is not None and e.get("status") == "proposed":
        fx.save(fx_home(), current)                 # a revision dropped: the accepted one stays
        _fx_sound(current)
        return JSONResponse({"ok": True})
    if e.get("status") == "accepted" or current is not None:
        raise HTTPException(400, "that effect is accepted — remove it instead")
    # a proposal, or a removed effect: gone for good, files and all
    (fx_home() / f"{fx_id}.json").unlink(missing_ok=True)
    shutil.rmtree(fx_home() / fx_id, ignore_errors=True)
    return JSONResponse({"ok": True})


@app.post("/api/fx/remove")
async def api_fx_remove(request: Request) -> JSONResponse:
    body = await request.json()
    fx_id = str(body.get("id") or "")
    edl = read_edl()
    before = list(edl.get("effects") or [])
    after = [x for x in before if x.get("id") != fx_id]
    if len(after) == len(before):
        raise HTTPException(404, f"no accepted effect {fx_id}")
    edl["effects"] = after
    write_edl(edl)
    # out of the cut, not gone: the file and its renders stay, marked removed, so
    # Restore puts it back exactly as it was; Discard is what deletes for good
    gone = next(x for x in before if x.get("id") == fx_id)
    gone["status"] = "removed"
    fx.save(fx_home(), gone)
    return JSONResponse({"ok": True, "effect": _fx_urls(gone)})


@app.put("/api/fx/{fx_id}")
async def api_fx_update(fx_id: str, request: Request) -> JSONResponse:
    """The human's edits: a nudged event, a moved anchor, a changed spec. Validated
    like everything else; the checklist is cleared because it no longer describes
    this effect."""
    body = await request.json()
    e = _fx_current(fx_id)
    merged = dict(e)
    for k in ("events", "overlay", "sound", "name", "note"):
        if k in body:
            merged[k] = body[k]
    merged.pop("verify", None)
    try:
        new = fx.validate_effect(merged, _fx_segments())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _fx_put(new)
    if body.get("sound") is not None:
        _fx_sound(new)
    _fx_autoverify(fx_id)
    return JSONResponse(_fx_urls(new))


@app.get("/api/fx/{fx_id}/{name}")
def api_fx_file(fx_id: str, name: str, request: Request) -> Response:
    if name not in ("sound.wav", "proof.mp4", "base.mp4", "strip.jpg", "ref.png"):
        raise HTTPException(404, "no such file")
    p = fx_home() / fx_id / name
    if not p.exists():
        raise HTTPException(404, f"{name} is not there yet")
    return ranged_file(p, request)


@app.get("/fx.js")
def fxjs() -> Response:
    """The FX tool, the monitor overlay and the sketch (INTAKE M12)."""
    return Response((HERE / "static" / "fx.js").read_text(encoding="utf-8"),
                    media_type="application/javascript", headers=NO_STORE)


@app.get("/fx.css")
def fxcss() -> Response:
    return Response((HERE / "static" / "fx.css").read_text(encoding="utf-8"),
                    media_type="text/css", headers=NO_STORE)


@app.get("/switcher.js")
def switcherjs() -> Response:
    """The bin-and-cut switcher every screen's header carries; one file, three pages."""
    return Response((HERE / "static" / "switcher.js").read_text(encoding="utf-8"),
                    media_type="application/javascript", headers=NO_STORE)


@app.get("/cli.js")
def clijs() -> Response:
    """The banner every screen carries when the Claude CLI needs Karl at a terminal."""
    return Response((HERE / "static" / "cli.js").read_text(encoding="utf-8"),
                    media_type="application/javascript", headers=NO_STORE)


@app.get("/dock.js")
def dockjs() -> Response:
    """The board's dock (INTAKE M11): the rail of tools, the keys overlay, the popover."""
    return Response((HERE / "static" / "dock.js").read_text(encoding="utf-8"),
                    media_type="application/javascript", headers=NO_STORE)


@app.get("/deep.js")
def deepjs() -> Response:
    """The coverage strip and Look deeper (INTAKE M15), shared by the three screens."""
    return Response((HERE / "static" / "deep.js").read_text(encoding="utf-8"),
                    media_type="application/javascript", headers=NO_STORE)


@app.get("/grade.js")
def gradejs() -> Response:
    """The monitor's grade (INTAKE M10, I10.4): the WebGL LUT over the live video."""
    return Response((HERE / "static" / "grade.js").read_text(encoding="utf-8"),
                    media_type="application/javascript", headers=NO_STORE)


# ---------------------------------------------------------------- edits (INTAKE M13)
#
# Operations on the cut itself — slow motion, an extension, a new clip. Karl,
# 2026-09-20: *"the effect tool itself, it needs to be able to make changes like this
# and even broader ones like slow motion or extension or creating new clips."*
# `roughcut/edits.py` is the vocabulary and the pure apply; this is the part that
# touches disk. A generated clip (black, a colour, a freeze frame) is a real file
# under `work/generated/<bin>/` — per bin like the proxies, because a still is cut
# from one bin's footage — listed among the project's clips so the monitor plays it,
# the Ask can design on it and the render cuts it like any other. The model never
# writes ffmpeg or a filename: `edits.generated_name` names the file, this makes it.

GEN_W, GEN_H, GEN_FPS = 1280, 720, 24      # a proxy's size: the monitor plays these
GEN_SLOTS = threading.BoundedSemaphore(2)   # two ffmpegs at once, like the posters


def generated_dir() -> Path:
    d = STATE.get("generated_dir") or (STATE["work"] / "generated" / STATE["footage"].name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def generated_spec_path(name: str) -> Path:
    """The record of what a generated file is (its `apply_ops` spec plus the probed
    duration), beside the file, so a project reopened later still knows a black
    slide from a freeze frame without probing or guessing from the name."""
    return generated_dir() / f"{Path(name).stem}.json"


def generated_label(spec: dict) -> str:
    """One line for the card and the Ask: `generated: black 2.0 s`."""
    kind = spec.get("kind")
    s = float(spec.get("seconds") or 0.0)
    if kind == "colour":
        return f"generated: colour {spec.get('color') or '#000000'} {s:.1f} s"
    if kind == "still":
        return (f"generated: still of {spec.get('from_clip') or '?'} at "
                f"{float(spec.get('at') or 0.0):.2f} s, {s:.1f} s")
    return f"generated: black {s:.1f} s"


def _generated_source(clip: str) -> Path:
    """Where a still's frame comes from: the clip's proxy (the frame the monitor
    showed when the human chose it), the source when there is no proxy yet, or a
    generated clip's own file when the still is of a slide."""
    if edits.is_generated(clip):
        p = generated_dir() / clip
    else:
        p = STATE["proxy_dir"] / f"{Path(clip).stem}.mp4"
        if not p.exists():
            p = clip_path(clip)
    if not p.exists():
        raise RuntimeError(f"no footage to take a still from: {clip}")
    return p


_GEN_ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-pix_fmt", "yuv420p", "-r", str(GEN_FPS),
               "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
               "-shortest", "-movflags", "+faststart"]


def materialise_generated(spec: dict) -> Path:
    """Make `gen_<kind>_<key>.mp4` from one entry of `apply_ops(...)["generated"]`
    (`clip`, `kind`, `seconds`, `color`, `from_clip`, `at`) and return its path.
    Idempotent: a file that exists is reused, so the same slide proposed twice is one
    encode. Black / colour: lavfi `color` + `anullsrc`. Still: one frame of the source
    clip's proxy at `at` to a PNG, looped for `seconds` over silence. 1280×720,
    libx264 yuv420p, aac — what a proxy is, so the monitor and the render treat it
    as one. Raises RuntimeError with ffmpeg's last words."""
    kind = spec.get("kind")
    if kind not in edits.KINDS:
        raise RuntimeError(f"unknown generated kind {kind!r}")
    seconds = float(spec["seconds"])
    name = spec.get("clip") or edits.generated_name(
        kind, seconds, color=spec.get("color"), from_clip=spec.get("from_clip"),
        at=spec.get("at"))
    dest = generated_dir() / name
    if dest.exists() and dest.stat().st_size:
        return dest
    with GEN_SLOTS:
        if dest.exists() and dest.stat().st_size:     # made while we waited
            return dest
        tmp = dest.with_name(f"{dest.stem}.{os.getpid()}.part.mp4")
        png = None
        if kind == "still":
            src = _generated_source(spec["from_clip"])
            at = max(0.0, float(spec.get("at") or 0.0))
            png = dest.with_suffix(".png")
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-nostdin", "-ss", f"{at:.3f}",
                 "-i", str(src), "-frames:v", "1",
                 "-vf", f"scale={GEN_W}:{GEN_H}:force_original_aspect_ratio=decrease,"
                        f"pad={GEN_W}:{GEN_H}:(ow-iw)/2:(oh-ih)/2,format=rgb24",
                 str(png)], capture_output=True, text=True)
            if r.returncode != 0 or not png.exists() or not png.stat().st_size:
                png.unlink(missing_ok=True)
                raise RuntimeError(f"still failed for {src.name} at {at:.2f}s: {r.stderr[-300:]}")
            cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin",
                   "-loop", "1", "-framerate", str(GEN_FPS), "-i", str(png),
                   "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                   "-t", f"{seconds:.3f}", *_GEN_ENCODE, str(tmp)]
        else:
            hexcolour = (spec.get("color") or "#000000").lstrip("#")
            cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin",
                   "-f", "lavfi", "-i",
                   f"color=c=0x{hexcolour}:s={GEN_W}x{GEN_H}:r={GEN_FPS}:d={seconds:.3f}",
                   "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                   "-t", f"{seconds:.3f}", *_GEN_ENCODE, str(tmp)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if png is not None:
            png.unlink(missing_ok=True)
        if r.returncode != 0 or not tmp.exists() or not tmp.stat().st_size:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"generated clip failed ({name}): {r.stderr[-300:]}")
        tmp.replace(dest)
        record = {k: spec.get(k) for k in ("kind", "seconds", "color", "from_clip", "at")}
        record["clip"] = name
        record["duration"] = probe_duration(dest) or seconds
        generated_spec_path(name).write_text(json.dumps(record, indent=1), encoding="utf-8")
    return dest


GAP_MAX_S = 3600.0


@app.post("/api/gap")
async def api_gap(request: Request) -> JSONResponse:
    """The black a gap on V1 is made of (verdict 4, punch 9: a clip dropped at 10 s on an
    empty V1 leaves 10 s of nothing before it, as Premiere's does). A generated black clip
    of `seconds` (black picture, silence) — the M13 generated clip, so the monitor plays it
    and the render cuts it like any other — returned with its clip record for the board's
    clip list. Idempotent: the same length is the same file."""
    body = await request.json()
    try:
        seconds = round(float(body.get("seconds")), 3)
    except (TypeError, ValueError):
        raise HTTPException(400, "seconds must be a number")
    if not (edits.MIN_GEN_S - 1e-9 <= seconds <= GAP_MAX_S):
        raise HTTPException(400, f"a gap is {edits.MIN_GEN_S:g}–{GAP_MAX_S:g} s")
    spec = {"kind": "black", "seconds": seconds, "color": None, "from_clip": None, "at": None}
    try:
        path = await asyncio.to_thread(materialise_generated, spec)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))
    rec = generated_clips({path.name}).get(path.name)
    return JSONResponse({"clip": path.name, "seconds": seconds, "record": rec})


def _generated_spec(name: str) -> dict:
    p = generated_spec_path(name)
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except ValueError:
            pass
    # a file with no record (copied in by hand): what its name says, and a probe
    m = re.fullmatch(r"gen_(black|colour|still)_[0-9a-f]+", Path(name).stem)
    kind = m.group(1) if m else "black"
    dur = probe_duration(generated_dir() / name) if (generated_dir() / name).exists() else None
    return {"kind": kind, "seconds": dur, "color": None, "from_clip": None, "at": None,
            "clip": name, "duration": dur}


def generated_clips(referenced: set[str] | None = None) -> dict[str, dict]:
    """The generated files as `project_payload` lists a clip: every `gen_*.mp4` in the
    dir, plus any a segment names that is not there yet (listed as `missing`, so the
    board shows the shot rather than losing it). Same keys as a footage clip — the
    card, the monitor and the Ask read them without knowing — with an empty
    transcript, a summary that says what it is and the spec under `generated`."""
    referenced = referenced or set()
    d = STATE.get("generated_dir") or (STATE["work"] / "generated" / STATE["footage"].name)
    names = [p.name for p in sorted(d.glob("gen_*.mp4")) if p.name.count(".") == 1] if d.is_dir() else []
    names += sorted(c for c in referenced if edits.is_generated(c) and c not in names)
    # keyed by path, not name: a re-point to another bin must not serve this one's
    # lengths (the same reason configure() clears the footage caches)
    cache: dict = STATE.setdefault("gen_durations", {})
    out: dict[str, dict] = {}
    for name in names:
        present = (d / name).exists()
        spec = _generated_spec(name) if present else None
        if spec is not None:
            key = str(d / name)
            if key not in cache:
                cache[key] = spec.get("duration") or probe_duration(d / name)
            duration = cache[key]
        else:
            spec, duration = {"kind": "black", "seconds": None, "color": None,
                              "from_clip": None, "at": None, "clip": name}, None
        spec = {k: spec.get(k) for k in ("clip", "kind", "seconds", "color", "from_clip", "at")}
        if spec["seconds"] is None:
            spec["seconds"] = duration
        stem = Path(name).stem
        out[name] = {
            "clip": name, "stem": stem, "duration": duration,
            "proxy": f"/media/generated/{name}" if present else "",
            "poster": f"/media/poster/{stem}.jpg" if present else "",
            "transcript": [], "visual": {}, "captured": None, "candidates": [],
            # The same keys a footage clip's summary has (the Ask's clip block and the
            # mixer read them by name), and the one line that says what this is.
            "summary": {"speech_fraction": 0.0, "wind_dominant_fraction": 0.0,
                        "integrated_lufs": None, "audio_usable": False,
                        "generated": generated_label(spec)},
            "generated": spec,
            "used": name in referenced,
        }
        if not present:
            out[name]["missing"] = True
    return out


@app.get("/media/generated/{name}")
def media_generated(name: str, request: Request) -> Response:
    """A generated clip, played the way a proxy is (it is one, in every way that
    matters to the monitor). `/media/proxy/` looks in the proxy dir and would not
    find it; `project_payload` points a generated clip's `proxy` here."""
    return ranged_file(generated_dir() / Path(name).name, request)


# ---- the ops themselves: preview writes nothing, apply writes once

def _edits_cut() -> tuple[dict, list[dict], dict]:
    """The cut on disk (with ids) and the clips it can name — footage and generated —
    for their lengths."""
    payload = project_payload()
    edl = read_edl()
    ensure_segment_ids(edl)
    return edl, edl.get("segments") or [], payload["clips"]


def _edits_shape(res: dict) -> dict:
    return {k: res[k] for k in ("segments", "id_map", "generated", "changed", "words")}


def _rekey_effects(effects: list[dict], before: list[dict], after: list[dict]) -> list[dict]:
    """Accepted effects follow their events through an edit. A shot that was split
    leaves its effect on the piece that holds most of its events (the original id on
    a tie); events that landed in another piece get a copy of the effect keyed to
    that piece with just those events. An effect whose shot left the cut is dropped —
    `validate_effect` would refuse the next save otherwise."""
    before_by_id = {str(s.get("id")): s for s in before}
    out: list[dict] = []
    for e in effects:
        sid = str(e.get("shot"))
        orig = before_by_id.get(sid)
        if orig is None:
            out.append(e)                            # not ours to judge
            continue
        pieces = [s for s in after
                  if s["clip"] == orig["clip"]
                  and (str(s.get("id")) == sid
                       or (str(s.get("id")) not in before_by_id
                           and float(s["in"]) >= float(orig["in"]) - 1e-6
                           and float(s["out"]) <= float(orig["out"]) + 1e-6))]
        if not pieces:
            continue                                 # the shot is gone; so is the effect
        events = e.get("events") or []

        def held(p: dict) -> list[dict]:
            return [ev for ev in events if float(p["in"]) <= float(ev["t"]) < float(p["out"])]

        home = max(pieces, key=lambda p: (len(held(p)), str(p.get("id")) == sid, -pieces.index(p)))
        out.append(e if str(home.get("id")) == sid else {**e, "shot": str(home["id"])})
        for p in pieces:
            if p is home:
                continue
            evs = held(p)
            if not evs:
                continue
            copy = {k: v for k, v in e.items() if k not in ("verify", "previous", "history")}
            copy.update({"id": fx.new_id(), "shot": str(p["id"]), "events": evs})
            out.append(copy)
    return out


def apply_edits(ops) -> dict:
    """What Accept calls (and `POST /api/edits/apply`): the ops validated against the
    cut on disk, applied, every `new:n` minted with `segment_id()`, every generated
    clip materialised, the accepted effects re-keyed to the pieces that hold their
    events, and the EDL's segments written — once. Raises ValueError with a sentence
    for a bad op (nothing written). Returns `apply_ops`'s shape with `id_map` filled
    in, plus `before` (the segments as they were — the board's undo does not cover
    this write, `POST /api/edits/undo {before}` does), `ops` (the clean list) and,
    when the effects changed, `before_effects` / `effects`."""
    edl, segments, clips = _edits_cut()
    clean = edits.validate_ops(ops, segments, clips)
    res = edits.apply_ops(segments, clips, clean)
    before = [dict(s) for s in segments]
    id_map = {pid: segment_id() for pid in res["id_map"]}
    for s in res["segments"]:
        if s["id"] in id_map:
            s["id"] = id_map[s["id"]]
    res["changed"] = [id_map.get(c, c) for c in res["changed"]]
    res["id_map"] = id_map
    # The files first: an ffmpeg that fails leaves the cut exactly as it was.
    for g in res["generated"]:
        try:
            materialise_generated(g)
        except RuntimeError as exc:
            raise ValueError(f"could not make {g['clip']}: {exc}") from exc
    before_effects = list(edl.get("effects") or [])
    effects_after = _rekey_effects(before_effects, before, res["segments"])
    edl["segments"] = res["segments"]
    if before_effects:
        edl["effects"] = effects_after
    if edl.get("selects") is not None or edl.get("floor") is not None:
        selects.sync_timeline(edl)
    write_edl(edl)
    out = _edits_shape(res)
    out["before"] = before
    out["ops"] = clean
    if effects_after != before_effects:
        out["before_effects"] = before_effects
        out["effects"] = effects_after
    return out


@app.post("/api/edits/preview")
async def api_edits_preview(request: Request) -> JSONResponse:
    """The cut as it would be after the ops — nothing written, no file made, the new
    shots still `new:n`. A bad op is a 400 with the sentence."""
    body = await request.json()
    _, segments, clips = _edits_cut()
    try:
        clean = edits.validate_ops(body.get("ops"), segments, clips)
        res = edits.apply_ops(segments, clips, clean)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    out = _edits_shape(res)
    out["ops"] = clean
    return JSONResponse(out)


@app.post("/api/edits/apply")
async def api_edits_apply(request: Request) -> JSONResponse:
    """`apply_edits` over HTTP: the same shape as preview with the ids minted, the
    files made and the cut written; `before` is what undo takes back."""
    body = await request.json()
    try:
        out = await asyncio.to_thread(apply_edits, body.get("ops"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return JSONResponse(out)


@app.post("/api/edits/undo")
async def api_edits_undo(request: Request) -> JSONResponse:
    """The segments back as `apply` reported them in `before`, validated the way a
    save is. `effects`, when given, are restored the same way (a bad one is a 400);
    when not, the accepted effects that still name a shot in the restored cut are
    kept and the rest — copies keyed to shots that no longer exist — are dropped."""
    body = await request.json()
    before = body.get("before")
    if not isinstance(before, list) or not before:
        raise HTTPException(400, "undo needs `before`: the segments as they were")
    clean = clean_segments(before)
    edl = read_edl()
    edl["segments"] = clean
    if isinstance(body.get("effects"), list):
        try:
            edl["effects"] = [fx.validate_effect(e, clean) for e in body["effects"]]
        except ValueError as exc:
            raise HTTPException(400, f"effects: {exc}")
    elif edl.get("effects"):
        ids = {str(s["id"]) for s in clean}
        edl["effects"] = [e for e in edl["effects"] if str(e.get("shot")) in ids]
    if edl.get("selects") is not None or edl.get("floor") is not None:
        selects.sync_timeline(edl)
    write_edl(edl)
    return JSONResponse({"ok": True, "segments": clean})


# ------------------------------------------------------------ import (wave 12, K3 b)
#
# REDESIGN-GOAL dimension 13, Karl 2026-10-16: *"there is no clear way to add new
# footage."* Footage comes in three ways, all into the open bin, none needing a restart:
#
#   * POST /api/import {paths}: files, or every video under a folder, on the machine that
#     runs the board, referenced in place (never copied) — File › Import and the Media
#     Browser (GET /api/media/browse lists a folder: names, sizes, dates; no ffprobe);
#   * POST /api/upload {files} then PUT /api/upload/{batch}/{i} per file: from the
#     viewer's own computer (Karl opens foxtrot's board from a laptop), each file's bytes
#     streamed to disk under the bin's --work media dir in 4 MB writes off the event loop,
#     never held in memory; the batch is a job in /api/jobs, so the header's job row shows
#     it and a second tab sees it;
#   * a file dropped into the bin's folder by hand, as before.
#
# Whatever lands goes through `media_added`: the snapshots see it, its free facts are
# probed (duration, rate, size), its poster is made, previews start, and the free part of
# the index (probe, telemetry, the sound, proxies) starts — the priced look never does on
# its own (INTAKE): it waits for Run first pass, whose panel prices it.

IMPORTS: dict[str, progress.Job] = {}       # upload batches, as jobs (kind "import")
UPLOADS: dict[str, dict] = {}               # batch -> {rows, job, last, got, total}
UPLOAD_WRITE = 4 << 20                      # bytes per write (and the most held in memory)
UPLOAD_SPARE = 512 << 20                    # what an upload must leave free on the disk
UPLOAD_STALL_S = 30.0                       # a batch with nothing arriving this long ends
IMPORT_MAX_PATHS = 500
UPLOAD_MAX_FILES = 500


def _clip_origin(clip: str, sources: mediamod.Sources | None = None) -> dict:
    """Where a clip lives: `source` folder | import | upload, and its `path`."""
    e = (sources or media_sources()).entry(clip)
    if e is None:
        return {"source": "folder", "path": str(STATE["footage"] / clip)}
    return {"source": e.get("kind") or "import", "path": e["path"],
            "came_as": e.get("from") or Path(e["path"]).name}


def media_changed() -> None:
    """The clip list changed: every snapshot is rebuilt on its next read."""
    with SNAP["lock"]:
        SNAP["gen"] += 1
        SNAP["rearm"] = True


def media_added(clips: list[str]) -> dict:
    """New clips are in the bin: the free work on them starts now, in the background.
    Returns {index: the job id of the index that will hear them, or None}."""
    clips = [c for c in clips if c]
    if not clips:
        return {"index": None}
    media_changed()
    warm_clip_facts(clips)                   # the Project panel's columns: length, rate, size
    threading.Thread(target=_import_posters, args=(clips, STATE["footage"]),
                     daemon=True).start()
    kick_previews()                          # previews on a GPU; the walk re-reads the list
    MEDIA["kick"].set()
    return {"index": start_free_index()}


def _import_posters(clips: list[str], footage: Path) -> None:
    """Each new clip's card picture, at the frame the panel will ask for (1 s in), made
    before it asks: the panel's request then finds it on disk or joins this build."""
    for clip in clips:
        if STATE.get("footage") != footage:
            return
        try:
            clip_duration(clip)
            with procscope.active(media_scope()):
                ensure_poster(Path(clip).stem, poster_time(clip), slots=WALK_POSTER_SLOTS)
        except (RuntimeError, OSError) as exc:
            print(f"  !! poster: {clip}: {exc}", flush=True)


def start_free_index() -> str | None:
    """The free part of the index for new footage: join a running index (it takes the
    new clips in on its next turn), else start one that holds the priced stages for this
    run (`FREE_ONLY`). Off where nothing is built behind the human's back (--no-proxies,
    the suite) unless `STATE["index_on_import"]` says otherwise."""
    if not STATE.get("index_on_import", STATE.get("proxies_enabled", True)):
        return None
    live = next((x for x in INDEXES.values() if x["state"] not in progress.TERMINAL), None)
    if live is not None:
        if live.get("cancel"):
            return None
        want = index_ask_intake(live)
        threading.Thread(target=_intake_after, args=(live, want), daemon=True).start()
        return live["id"]
    if not footage_clips():
        return None
    job = index_entry(free=True)
    threading.Thread(target=_index_run, args=(job, ""), daemon=True).start()
    return job


def _intake_after(entry: progress.Job, want: int) -> None:
    """An index asked to take new footage in that ended first (its last stage landed as
    the import did): a free run for what it missed."""
    while entry["state"] not in progress.TERMINAL:
        time.sleep(0.5)
    if int(entry.get("took") or 0) < want and not entry.get("cancel") \
            and not any(x["state"] not in progress.TERMINAL for x in INDEXES.values()):
        start_free_index()


def _in_project_paths() -> set[str]:
    return ({os.path.realpath(STATE["footage"] / c) for c in folder_clips()}
            | media_sources().real_paths())


@app.get("/api/media/browse")
def api_media_browse(path: str = "") -> JSONResponse:
    """One folder of the machine that runs the board, as the Media Browser lists it: its
    folders and video files (name, size, modified; `in_project` on what the bin already
    has), the places to start from (`roots`), and this machine's name. A plain `def`, so
    it runs on a worker thread: a big folder never holds the event loop."""
    want = (path or "").strip() or str(STATE["footage"].parent)
    try:
        d = mediamod.browse(Path(want), in_project=_in_project_paths())
    except FileNotFoundError:
        raise HTTPException(404, f"no such folder: {want}")
    except NotADirectoryError:
        raise HTTPException(400, f"not a folder: {want}")
    except PermissionError:
        raise HTTPException(403, f"no permission to read {want}")
    except OSError as exc:
        raise HTTPException(400, f"cannot read {want}: {exc.strerror or exc}")
    d["roots"] = mediamod.roots(STATE["footage"])
    d["host"] = socket.gethostname()
    d["bin"] = {"name": STATE["footage"].name, "folder": str(STATE["footage"])}
    return JSONResponse(d)


@app.get("/api/media/sources")
def api_media_sources() -> JSONResponse:
    """What was imported or uploaded into this bin, where each file is, and whether it
    is there now (an unplugged drive's clips are not listed in /api/clips)."""
    rows = []
    for e in media_sources().entries:
        p = Path(e["path"])
        rows.append({**e, "present": p.is_file()})
    return JSONResponse({"bin": STATE["footage"].name, "folder": str(STATE["footage"]),
                         "entries": rows, "uploads": str(mediamod.uploads_dir(
                             STATE["work"], STATE["footage"].name))})


def _import_paths(paths: list[str]) -> dict:
    """POST /api/import's work, off the event loop: expand, de-duplicate, name, save."""
    files, skipped = mediamod.scan([Path(p) for p in paths])
    own = {os.path.realpath(STATE["footage"] / c) for c in folder_clips()}
    keep = []
    for f, hint in files:
        if os.path.realpath(f) in own:
            skipped.append({"path": str(f), "why": "already in the project (the bin's folder)"})
        else:
            keep.append((f, hint))
    added, more = media_sources().add(keep, "import", folder_clips())
    started = media_added(added)
    return {"added": added, "skipped": skipped + more, "clips": len(footage_clips()),
            "index": started["index"]}


@app.post("/api/import")
async def api_import(request: Request) -> JSONResponse:
    """Import files or folders from the machine that runs the board into the open bin,
    referenced in place (Premiere's File › Import). Body `{paths: [...]}`: a file is
    itself, a folder is every video under it. Answers at once with the clips added (each
    one's name: its own, or `<stem>-<folder>` when the stem is taken) and what was
    skipped and why; posters, facts, previews and the free index follow in the
    background."""
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "expected a JSON object")
    paths = body.get("paths") if isinstance(body, dict) else None
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, list) or not paths \
            or not all(isinstance(p, str) and p.strip() for p in paths):
        raise HTTPException(400, "paths: a list of files or folders to import")
    if len(paths) > IMPORT_MAX_PATHS:
        raise HTTPException(400, f"at most {IMPORT_MAX_PATHS} paths at once")
    return JSONResponse(await asyncio.to_thread(_import_paths, [p.strip() for p in paths]))


@app.post("/api/media/remove")
async def api_media_remove(request: Request) -> JSONResponse:
    """Take imported or uploaded clips out of the bin (Premiere's Clear). A clip a cut
    uses is refused; a referenced file is never touched; an uploaded file goes to the
    uploads' trash/, where a hand can get it back. The bin's own folder is never edited."""
    body = await request.json()
    clips = body.get("clips") if isinstance(body, dict) else None
    if not isinstance(clips, list) or not all(isinstance(c, str) for c in clips):
        raise HTTPException(400, "clips: which clips to remove")
    src = media_sources()
    mine = [c for c in clips if src.entry(c) is not None]
    used = {s.get("clip") for s in read_edl().get("segments") or []}
    if used & set(mine):
        raise HTTPException(409, "the cut uses " + ", ".join(sorted(used & set(mine)))
                            + " — take it out of the cut first")

    def work() -> list[dict]:
        gone = src.remove(mine)
        trash = mediamod.uploads_dir(STATE["work"], STATE["footage"].name) / "trash"
        for e in gone:
            if e.get("kind") == "upload" and Path(e["path"]).is_file():
                trash.mkdir(parents=True, exist_ok=True)
                Path(e["path"]).replace(trash / f"{int(time.time())}-{Path(e['path']).name}")
        media_changed()
        return gone

    gone = await asyncio.to_thread(work)
    return JSONResponse({"removed": [e["clip"] for e in gone],
                         "not_imported": [c for c in clips if c not in mine],
                         "clips": len(footage_clips())})


def _disk_free(d: Path) -> int:
    p = d
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 0


def _fmt_bytes(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _upload_tick(up: dict) -> None:
    """The batch's job, from its rows: bytes in, files landed, ended when every file has
    landed or failed. Called per chunk, so it only adds and compares."""
    job: progress.Job = up["job"]
    if job["state"] in progress.TERMINAL:
        return
    rows = [r for r in up["rows"] if not r.get("skip")]
    got = up["landed_bytes"] + sum(r["got"] for r in rows if r["state"] == "uploading")
    total = max(1, up["total"])
    landed = sum(1 for r in rows if r["state"] == "done")
    job["done"] = landed
    job["got"] = got
    cur = next((r for r in rows if r["state"] == "uploading"), None)
    detail = (f"{cur['clip']} · {_fmt_bytes(got)} of {_fmt_bytes(up['total'])}" if cur
              else f"{landed} of {len(rows)} files in")
    job.advance("upload", got / total, detail=detail)
    if all(r["state"] in ("done", "failed") for r in rows):
        failed = [r for r in rows if r["state"] == "failed"]
        if landed:
            job.finish("done", detail=f"{landed} clip{'s' if landed != 1 else ''} imported"
                       + (f" — {len(failed)} failed" if failed else ""))
        else:
            job.finish("failed", detail="failed: no file arrived whole")


def _upload_eta(up: dict) -> float | None:
    job = up["job"]
    el = job.elapsed()
    got = float(job.get("got") or 0)
    if el < 1.0 or got <= 0:
        return None
    return max(0.0, (up["total"] - got) / (got / el))


def _upload_watch(batch: str) -> None:
    """A batch whose page went away (closed, reloaded) ends instead of holding the
    header and the bin switch: nothing uploading and nothing new for UPLOAD_STALL_S."""
    while True:
        time.sleep(1.0)
        up = UPLOADS.get(batch)
        if up is None or up["job"]["state"] in progress.TERMINAL:
            return
        busy = any(r["state"] == "uploading" for r in up["rows"])
        if not busy and time.time() - up["last"] > UPLOAD_STALL_S:
            for r in up["rows"]:
                if not r.get("skip") and r["state"] == "waiting":
                    r["state"] = "failed"
                    r["why"] = "never arrived"
            _upload_tick(up)


@app.post("/api/upload")
async def api_upload_plan(request: Request) -> JSONResponse:
    """Start an upload from the viewer's computer. Body `{files: [{name, size, rel?}]}`
    (`rel`: the path inside a dropped folder, whose folder name tells two same-named
    files apart). Answers `{batch, files: [{i, clip, skip?}], bytes}`: the clip name each
    file will have, or why it is skipped (not a video; the same file, by name and size,
    uploaded already). Then PUT each file's bytes to /api/upload/{batch}/{i}. Refused when
    the disk would be left with less than 512 MB."""
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "expected a JSON object")
    files = body.get("files") if isinstance(body, dict) else None
    if not isinstance(files, list) or not files or len(files) > UPLOAD_MAX_FILES:
        raise HTTPException(400, f"files: 1 to {UPLOAD_MAX_FILES} of {{name, size}}")
    src = media_sources()
    folder = folder_clips()
    rows, pending = [], set()
    for i, f in enumerate(files):
        if not isinstance(f, dict) or not isinstance(f.get("name"), str):
            raise HTTPException(400, f"files[{i}]: {{name, size}}")
        try:
            size = int(f.get("size"))
        except (TypeError, ValueError):
            raise HTTPException(400, f"files[{i}].size: bytes")
        name = Path(str(f["name"]).replace("\\", "/")).name
        if size < 0 or not name:
            raise HTTPException(400, f"files[{i}]: a name and a size")
        if not mediamod.is_video(name):
            rows.append({"i": i, "name": name, "size": size, "skip": "not a video file"})
            continue
        dup = src.find_upload(name, size)
        if dup:
            rows.append({"i": i, "name": name, "size": size, "clip": dup,
                         "skip": "already imported"})
            continue
        rel = str(f.get("rel") or "").replace("\\", "/")
        hint = Path(rel).parent.name if "/" in rel else "upload"
        clip = src.reserve(name, hint, folder, pending)
        pending.add(mediamod.stem_key(clip))
        rows.append({"i": i, "name": name, "size": size, "clip": clip, "rel": rel,
                     "state": "waiting", "got": 0})
    todo = [r for r in rows if not r.get("skip")]
    total = sum(r["size"] for r in todo)
    updir = mediamod.uploads_dir(STATE["work"], STATE["footage"].name)
    free = await asyncio.to_thread(_disk_free, updir)
    if todo and total + UPLOAD_SPARE > free:
        raise HTTPException(507, f"not enough space on {socket.gethostname()}: "
                            f"{_fmt_bytes(total)} to upload, {_fmt_bytes(free)} free")
    batch = uuid.uuid4().hex[:10]
    out = [{k: r[k] for k in ("i", "name", "size", "clip", "skip") if k in r} for r in rows]
    if not todo:
        return JSONResponse({"batch": None, "files": out, "bytes": 0})
    n = len(todo)
    job = progress.Job("import", f"Importing {n} file{'s' if n != 1 else ''}", id=batch,
                       state="running", detail="starting", total=n, done=0, got=0,
                       bytes=total)
    job.set_estimate(None, [progress.milestone("upload", "uploading", 1.0)], source="none")
    up = {"rows": rows, "job": job, "last": time.time(), "landed_bytes": 0, "total": total,
          "bin": STATE["footage"].name}
    job.eta_fn = lambda _j, up=up: _upload_eta(up)
    UPLOADS[batch] = up
    IMPORTS[batch] = job
    threading.Thread(target=_upload_watch, args=(batch,), daemon=True).start()
    return JSONResponse({"batch": batch, "files": out, "bytes": total, "job": batch})


@app.put("/api/upload/{batch}/{i}")
async def api_upload_file(batch: str, i: int, request: Request) -> JSONResponse:
    """One file's bytes, streamed to disk as they arrive: written in UPLOAD_WRITE pieces
    on a worker thread into the uploads' `.incoming/` (unwatched), renamed into place
    whole, then added to the bin. A short body, a long one, a closed connection or the
    batch's Stop leaves nothing behind."""
    up = UPLOADS.get(batch)
    if up is None or up.get("bin") != STATE["footage"].name:
        raise HTTPException(404, "no such upload")
    row = next((r for r in up["rows"] if r["i"] == i), None)
    if row is None or row.get("skip"):
        raise HTTPException(404, f"no file {i} in this upload")
    job = up["job"]
    if job["state"] in progress.TERMINAL:
        raise HTTPException(409, "this upload has ended")
    if row["state"] in ("uploading", "done"):
        raise HTTPException(409, f"{row['clip']} is {row['state']}")
    updir = mediamod.uploads_dir(STATE["work"], STATE["footage"].name)
    incoming = updir / ".incoming"
    await asyncio.to_thread(incoming.mkdir, parents=True, exist_ok=True)
    part = incoming / f"{row['clip']}.{batch}.part"
    dest = updir / row["clip"]
    row.update(state="uploading", got=0)
    up["last"] = time.time()
    f = await asyncio.to_thread(open, part, "wb")
    held: list[bytes] = []
    n_held = 0
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            row["got"] += len(chunk)
            now = up["last"] = time.time()
            if now - up.get("ticked", 0.0) > 0.25:      # the job's bar, four times a second
                up["ticked"] = now
                _upload_tick(up)
            if row["got"] > row["size"]:
                raise HTTPException(413, f"more than the {row['size']} bytes announced")
            if job["state"] in progress.TERMINAL:
                raise HTTPException(409, "the upload was stopped")
            held.append(chunk)
            n_held += len(chunk)
            if n_held >= UPLOAD_WRITE:
                data, held, n_held = b"".join(held), [], 0
                await asyncio.to_thread(f.write, data)
        if held:
            await asyncio.to_thread(f.write, b"".join(held))
        await asyncio.to_thread(f.close)
        if row["got"] != row["size"]:
            raise HTTPException(400, f"{row['got']} of {row['size']} bytes arrived")
        await asyncio.to_thread(os.replace, part, dest)
    except BaseException as exc:           # a disconnect, a Stop, a bad length: no trace
        await asyncio.to_thread(f.close)
        await asyncio.to_thread(part.unlink, missing_ok=True)
        row.update(state="failed", got=0,
                   why=str(getattr(exc, "detail", "") or type(exc).__name__)[:120])
        _upload_tick(up)
        raise
    row["state"] = "done"
    up["landed_bytes"] += row["size"]
    await asyncio.to_thread(media_sources().add_upload, row["clip"], dest, row["name"])
    started = await asyncio.to_thread(media_added, [row["clip"]])
    _upload_tick(up)
    return JSONResponse({"clip": row["clip"], "bytes": row["got"], "index": started["index"],
                         "clips": len(footage_clips())})


@app.post("/api/upload/{batch}/cancel")
def api_upload_cancel(batch: str) -> JSONResponse:
    """Stop an upload: the file in flight is dropped at its next chunk; what landed stays."""
    up = UPLOADS.get(batch)
    if up is None:
        raise HTTPException(404, "no such upload")
    job = up["job"]
    if job["state"] not in progress.TERMINAL:
        for r in up["rows"]:
            if not r.get("skip") and r["state"] == "waiting":
                r["state"] = "failed"
                r["why"] = "stopped"
        landed = sum(1 for r in up["rows"] if r.get("state") == "done")
        job.finish("cancelled", detail=f"stopped — {landed} file{'s' if landed != 1 else ''} "
                   "imported before the stop")
    return JSONResponse(job.snapshot())


# ------------------------------------------------------------------------ the AI's edits
#
# Redesign wave 12, lane AIOPS (REDESIGN-GOAL dimension 15, Karl's K3 d): the editor asks
# in words, from anywhere (the AI prompt, lane AIPROMPT), and the answer is a list of the
# pro tools' own operations (roughcut/actions.py), applied on the board through the same
# `window.tl` / `window.effects` calls a human edit makes — after a review, op by op
# (/modules/actions.js). Nothing here writes the cut: the server asks the model, checks
# the ops against the cut on disk, and keeps a record of the question, the answer and the
# review under `--work/assist/<bin>/`, as the Ask keeps its own.
#
#   POST /api/assist {text, scope}          → {job} at once; the model runs on a thread
#   GET  /api/assist/{job}                  → {status, kind, ops, lines, refused, preview,
#                                              plan_ask, text, summary, cost, error}
#   POST /api/assist/{job}/answer {accepted, rejected}   the review, recorded
#   POST /api/assist/{job}/cancel           stop waiting: the job reads `cancelled` at once
#                                              and whatever the call returns is dropped
#   GET  /api/assist/price?text=&scope=     the call's price before it is made (no model)
#   POST /api/assist/preview {ops, segments?, …}   the ops checked against the board's
#                                              cut as it is now (the review opens on it)
#
# `kind: "plan"` (a whole-cut rewrite) starts an ordinary Ask job with the model's brief;
# `plan_ask` is its id and the existing proposal panel reviews it.

from roughcut import actions as actionsmod  # noqa: E402
from roughcut import assist as assistmod  # noqa: E402

ASSISTS: dict[str, progress.Job] = {}
ASSIST_SCOPES = ("selection", "range", "sequence", "clip")
ASSIST_LOCK = threading.Lock()          # a job's end and its cancel: one of them, whole


def assist_home() -> Path:
    d = STATE["work"] / "assist" / STATE["asks"].name
    d.mkdir(parents=True, exist_ok=True)
    return d


def assist_state(payload: dict | None = None) -> dict:
    """What an op is checked against: the cut, its clips' lengths, the sequence's
    markers, In / Out, tracks, V2+ clips and transitions, the looks and the presets."""
    payload = payload if payload is not None else project_payload()
    presets = {k: v["label"] for k, v in EXPORT_PRESETS.items()}
    looks = [k for k, v in looks_library().items() if v.get("kind") != "broken"]
    return actionsmod.state_from(payload, looks=looks, presets=presets)


def clean_scope(raw) -> dict:
    """`{kind, ids?, in?, out?, clip?, playhead}` as WAVE12.md has it; anything else is
    the whole sequence."""
    if not isinstance(raw, dict):
        return {"kind": "sequence"}
    kind = raw.get("kind") if raw.get("kind") in ASSIST_SCOPES else "sequence"
    out: dict = {"kind": kind}
    if isinstance(raw.get("ids"), list):
        out["ids"] = [str(x)[:40] for x in raw["ids"][:200]]
    for k in ("in", "out", "playhead"):
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            out[k] = float(v)
    if raw.get("clip"):
        out["clip"] = str(raw["clip"])[:200]
    return out


def _assist_record_path(job: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{8,32}", job or ""):
        raise HTTPException(404, "no such job")
    return assist_home() / f"{job}.json"


def _assist_write(job: str, record: dict) -> None:
    path = _assist_record_path(job)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1), encoding="utf-8")
    tmp.replace(path)


def _assist_start_ask(note: str) -> str:
    """A whole-cut rewrite: an ordinary Ask job (the /api/ask path, the whole cut), so
    the proposal panel reviews it exactly as it reviews an Ask."""
    clips, payload = _ask_clips(None, drop_junk=True)
    if not clips:
        raise ValueError("no analysed clips yet — run the audio pass first")
    segments = payload["segments"]
    target = payload["target"]
    refuse_past_cap("re-cutting the sequence", ask_price("full")["usd"])
    job = uuid.uuid4().hex[:8]
    ASKS[job] = progress.Job(
        "ask", "Revising the cut" if segments else "Building the first cut", id=job,
        state="estimating", plan=None, code=0, detail="estimating how long this will take",
        ask_kind="revision" if segments else "first cut", estimate=None)
    threading.Thread(
        target=_ask_job,
        args=(job, segments, clips, payload.get("story", ""), note,
              (float(target[0]), float(target[1])), payload["events"], None),
        daemon=True).start()
    return job


def _assist_job(job: str, text: str, scope: dict) -> None:
    entry = ASSISTS[job]
    record = {"job": job, "created": time.time(), "text": text, "scope": scope}
    try:
        clips, payload = _ask_clips(None, drop_junk=True)
        state = assist_state(payload)
        entry["cost"] = assistmod.estimate_cost(
            assistmod.build_prompt(text, scope, state, clips))
        entry.note("asking the model")
        res = assistmod.run(text, scope, state, clips)
        if entry.get("state") == "cancelled":
            return                                   # stopped: the answer is not wanted
        if res["kind"] == "plan":
            res["plan_ask"] = _assist_start_ask(res.get("note") or text)
    except HTTPException as exc:
        if entry.get("state") == "cancelled":
            return
        entry.update(error=str(exc.detail), code=exc.status_code)
        entry.finish("failed", detail=str(exc.detail))
        _assist_write(job, {**record, "status": "failed", "error": str(exc.detail)})
        return
    except inference.BudgetExceeded as exc:
        if entry.get("state") == "cancelled":
            return
        entry.update(error=str(exc), code=429)
        entry.finish("failed", detail=str(exc))
        _assist_write(job, {**record, "status": "failed", "error": str(exc)})
        return
    except (inference.InferenceError, ValueError) as exc:
        if entry.get("state") == "cancelled":
            return
        entry.update(error=str(exc)[:300], code=502)
        entry.finish("failed", detail=str(exc)[:300])
        _assist_write(job, {**record, "status": "failed", "error": str(exc)[:300]})
        return
    except Exception as exc:  # noqa: BLE001 — a thread that dies must still end its job
        if entry.get("state") == "cancelled":
            return
        msg = f"{type(exc).__name__}: {exc}"[:300]
        entry.update(error=msg, code=500)
        entry.finish("failed", detail=msg)
        _assist_write(job, {**record, "status": "failed", "error": msg})
        return
    with ASSIST_LOCK:
        if entry.get("state") == "cancelled":
            return
        record.update(status="done", result=res)
        _assist_write(job, record)
        entry.update(result=res, cost=res.get("cost"))
        entry.finish("done", detail=res.get("summary") or res["kind"])


def _assist_view(job: str) -> dict:
    """The job as GET /api/assist/{job} answers it: live while it runs, from its record
    on disk after (so a restart or a reload keeps the answer)."""
    entry = ASSISTS.get(job)
    path = _assist_record_path(job)
    try:
        rec = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        rec = None
    if entry is None:
        if rec is None:
            raise HTTPException(404, "no such job")
        res = rec.get("result") or {}
        out = {"job": job, "status": rec.get("status") or "failed", "text_asked": rec.get("text"),
               "scope": rec.get("scope"), "error": rec.get("error")}
    else:
        snap = entry.snapshot()
        status = {"done": "done", "failed": "failed", "cancelled": "cancelled"}.get(snap["state"], "running")
        res = entry.get("result") or {}
        out = {"job": job, "status": status, "text_asked": entry.get("text"),
               "scope": entry.get("scope"), "error": entry.get("error"),
               "detail": snap.get("detail"), "elapsed_s": snap.get("elapsed_s"),
               "eta_s": snap.get("eta_s")}
        if status == "running" and entry.get("cost"):
            out["cost"] = entry["cost"]
    if rec is not None and rec.get("answer"):
        out["answer"] = rec["answer"]
    for k in ("kind", "ops", "lines", "refused", "index", "families", "preview", "plan_ask",
              "text", "note", "summary", "cost"):
        if k in res:
            out[k] = res[k]
    if out.get("plan_ask") and out["plan_ask"] in ASKS:
        a = ASKS[out["plan_ask"]].snapshot()
        out["plan_state"] = a.get("state")
        out["plan_detail"] = a.get("detail")
    return out


@app.post("/api/assist")
async def api_assist(request: Request) -> JSONResponse:
    """The editor's words in, a job out — at once. The model call, the cut's facts and
    the op checks all run on the job's thread, so this never waits on the model."""
    body = await request.json()
    text = str(body.get("text") or "").strip()[:assistmod.TEXT_MAX]
    if not text:
        raise HTTPException(400, "say what you want done")
    scope = clean_scope(body.get("scope"))
    job = uuid.uuid4().hex[:12]
    ASSISTS[job] = progress.Job(
        "assist", "Asking the AI", id=job, state="running",
        detail="reading the sequence", eta_s=assistmod.ETA_S, text=text, scope=scope,
        result=None, error=None, code=0)
    threading.Thread(target=_assist_job, args=(job, text, scope), daemon=True).start()
    return JSONResponse({"job": job, "status": "running", "scope": scope,
                         **config.cost_terms()})


@app.post("/api/assist/preview")
async def api_assist_preview(request: Request) -> JSONResponse:
    """The ops checked against the cut the board holds now — its `segments` (and, when
    sent, its markers, In / Out, transitions, V2+ clips and tracks) over the project's
    clips — so the review opens on what Accept would really do. Nothing is written."""
    body = await request.json()

    def run() -> dict:                 # the project's files are read off the event loop
        state = assist_state()
        for k in ("segments", "markers", "transitions", "overlays", "tracks"):
            if isinstance(body.get(k), (list, dict)):
                state[k] = body[k]
        for k in ("in", "out"):
            if k in body:
                state[k] = body[k]
        if isinstance(body.get("colour"), dict):
            state["colour"] = body["colour"]
        return actionsmod.check(body.get("ops"), state)
    return JSONResponse(await asyncio.to_thread(run))


@app.get("/api/assist/price")
def api_assist_price(text: str = "", scope: str = "") -> JSONResponse:
    """What an ask would cost before it is sent: the prompt it would build (the cut, the
    clips, the scope, the vocabulary) priced at the role's model — never a model call.
    `scope` is the JSON of the POST's scope. Declared before /api/assist/{job}."""
    try:
        sc = json.loads(scope) if scope else None
    except ValueError:
        sc = None
    clips, payload = _ask_clips(None, drop_junk=True)
    state = assist_state(payload)
    prompt = assistmod.build_prompt(text.strip()[:assistmod.TEXT_MAX] or "…", clean_scope(sc),
                                    state, clips)
    return JSONResponse(assistmod.estimate_cost(prompt))


@app.get("/api/assist/{job}")
def api_assist_status(job: str) -> JSONResponse:
    return JSONResponse(_assist_view(job))


@app.post("/api/assist/{job}/cancel")
def api_assist_cancel(job: str) -> JSONResponse:
    """Stop waiting for an answer (the prompt's Stop). The job reads `cancelled` at once
    and its record says so; the model call already sent finishes on its thread and what
    it returns is dropped — nothing reaches the review. A finished job is left as it is."""
    entry = ASSISTS.get(job)
    path = _assist_record_path(job)
    if entry is None:
        if not path.exists():
            raise HTTPException(404, "no such job")
        return JSONResponse({"ok": False, "status": json.loads(path.read_text(encoding="utf-8")).get("status")})
    with ASSIST_LOCK:
        if entry.get("state") in progress.TERMINAL:
            return JSONResponse({"ok": False, "status": entry["state"]})
        entry.update(error="cancelled by the editor")
        entry.finish("cancelled", detail="cancelled")
        _assist_write(job, {"job": job, "created": entry.get("started"), "text": entry.get("text"),
                            "scope": entry.get("scope"), "status": "cancelled",
                            "error": "cancelled by the editor"})
    return JSONResponse({"ok": True, "status": "cancelled"})


@app.post("/api/assist/{job}/answer")
async def api_assist_answer(job: str, request: Request) -> JSONResponse:
    """The review, on the record: which ops (indices into the answer's `ops`) the editor
    accepted and which they rejected. Applying them is the board's (one undo entry)."""
    body = await request.json()
    path = _assist_record_path(job)
    if not path.exists():
        if job in ASSISTS:
            raise HTTPException(409, "the answer is not in yet")
        raise HTTPException(404, "no such job")
    rec = json.loads(path.read_text(encoding="utf-8"))
    n = len(((rec.get("result") or {}).get("ops")) or [])

    def picks(key: str) -> list[int]:
        v = body.get(key) or []
        if not isinstance(v, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in v):
            raise HTTPException(400, f"{key} must be a list of op indices")
        bad = [i for i in v if not (0 <= i < n)]
        if bad:
            raise HTTPException(400, f"{key}: no op {bad[0]} in an answer of {n}")
        return sorted(set(v))

    acc, rej = picks("accepted"), picks("rejected")
    if set(acc) & set(rej):
        raise HTTPException(400, "an op cannot be both accepted and rejected")
    rec["answer"] = {"accepted": acc, "rejected": rej, "at": time.time()}
    _assist_write(job, rec)
    return JSONResponse({"ok": True, "answer": rec["answer"]})


# ------------------------------------------------------------------------ cuts
#
# One bin, several cuts. The EDL has always been the whole project file — the
# timeline, the story, the music bed and, since the intake, the pass's picks — and the
# board has always edited exactly one of them per bin. "Saving copies so we can try
# different things" (Karl, 2026-09-08) is a second file of the same shape. A copy is a
# snapshot of the *entire* file, never a partial one, so switching between cuts never
# merges anything and nothing about the file's shape changes: `assemble.py` renders a
# copy exactly as it renders the original. Everything that belongs to the bin rather
# than to a cut — proxies, sidecars, the visual pass, the journal, renders — is keyed
# by the footage folder and shared by every cut of it.
#
# On disk: the bin's first cut stays where it always was (`projects/<bin>.edl.json`,
# so nothing that exists moves), copies live beside it under
# `projects/<bin>/<slug>.edl.json`, and a deleted copy goes to `projects/<bin>/trash/`
# rather than away. The registry remembers which cut each bin was last open on, so
# opening a bin again lands on the cut you left it on — including one named with
# `--edl` on the command line, which used to be forgotten the moment you switched bins.

DEFAULT_CUT_NAME = "main"
CUT_SUFFIX = ".edl.json"


def cuts_dir(bin_name: str | None = None) -> Path:
    return STATE["work"] / "projects" / (bin_name or STATE["footage"].name)


def default_cut_path(bin_name: str | None = None) -> Path:
    return STATE["work"] / "projects" / f"{bin_name or STATE['footage'].name}{CUT_SUFFIX}"


def cut_slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-.").lower()[:60]


def cut_name_of(edl: dict, path: Path, bin_name: str | None = None) -> str:
    """What a cut is called: the name written into it when it was copied or renamed,
    else `main` for the bin's own file, else the file's stem (a cut named with --edl)."""
    meta = edl.get("cut") if isinstance(edl.get("cut"), dict) else {}
    if meta.get("name"):
        return str(meta["name"])
    if path == default_cut_path(bin_name):
        return DEFAULT_CUT_NAME
    name = path.name
    return name[:-len(CUT_SUFFIX)] if name.endswith(CUT_SUFFIX) else path.stem


def _registry() -> dict:
    try:
        known = json.loads(projects_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return known if isinstance(known, dict) else {}


def _cut_paths(bin_name: str) -> list[Path]:
    """Every cut file a bin has: its own, the copies beside it, and any cut named on
    the command line that the registry saw for this bin (kept so a `--edl` cut stays
    reachable after a copy of it is opened)."""
    paths: list[Path] = []
    if default_cut_path(bin_name).exists():
        paths.append(default_cut_path(bin_name))
    if cuts_dir(bin_name).is_dir():
        paths.extend(sorted(p for p in cuts_dir(bin_name).glob(f"*{CUT_SUFFIX}")
                            if p.is_file()))
    rec = _registry().get(bin_name)
    for raw in (rec.get("cuts") or []) if isinstance(rec, dict) else []:
        p = Path(raw)
        if p.is_file() and p not in paths:
            paths.append(p)
    return paths


def _cut_row(path: Path) -> dict | None:
    try:
        edl = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(edl, dict):
        return None
    meta = edl.get("cut") if isinstance(edl.get("cut"), dict) else {}
    segs = [s for s in (edl.get("segments") or []) if isinstance(s, dict)]
    try:
        duration = round(sum(float(s["out"]) - float(s["in"]) for s in segs), 2)
    except (KeyError, TypeError, ValueError):
        duration = 0.0
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = None
    return {
        "name": cut_name_of(edl, path), "path": str(path),
        "segments": len(segs), "duration_s": duration,
        "story": (edl.get("story") or "")[:120],
        "music": bool(edl.get("effects_music")),
        "created": meta.get("created"), "from": meta.get("from"),
        "modified": modified,
        "current": path == STATE["edl"], "default": path == default_cut_path(),
    }


def list_cuts() -> dict:
    """The cuts of the current bin, the one on the board first. Facts only."""
    paths = _cut_paths(STATE["footage"].name)
    if STATE["edl"] not in paths:           # named on the command line, wherever it lives
        paths.insert(0, STATE["edl"])
    rows = [r for r in (_cut_row(p) for p in paths) if r is not None]
    rows.sort(key=lambda r: (not r["current"], not r["default"],
                             -(r["created"] or 0), r["name"].lower()))
    return {"bin": STATE["footage"].name, "footage": str(STATE["footage"]),
            "current": next((r for r in rows if r["current"]), None), "cuts": rows}


def _known_cut(raw) -> Path:
    """The cut a request names, if it is one of this bin's — never an arbitrary file."""
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(400, "path: which cut")
    want = Path(raw).expanduser()
    for r in list_cuts()["cuts"]:
        if Path(r["path"]) == want:
            return want
    raise HTTPException(404, f"not a cut of {STATE['footage'].name}: {raw}")


def _cut_name_from(body) -> str:
    name = body.get("name") if isinstance(body, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(400, "name: what to call the cut")
    name = " ".join(name.split())[:60]
    if not cut_slug(name):
        raise HTTPException(400, f"name: nothing usable in {name!r}")
    return name


def _refuse_while_running(what: str) -> None:
    """The same rule bins have: a render or an Ask mid-flight belongs to the cut it
    started on, and an index writes into the EDL it was started against."""
    if any(j["state"] not in progress.TERMINAL for j in all_jobs()):
        raise HTTPException(409, f"a job is still running — wait for it before {what}")


def _write_cut(path: Path, edl: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(edl, indent=1), encoding="utf-8")
    tmp.replace(path)


def switch_cut(path: Path) -> None:
    """Point the board at another cut of the same bin. Everything per-bin stays as it
    is; only what the EDL itself carries is re-read."""
    STATE["edl"] = path
    STATE["edl_created"] = False
    STATE["orient"] = read_edl().get("orient", "auto")
    remember_project(STATE["footage"], path)


@app.get("/api/cuts")
def api_cuts() -> JSONResponse:
    return JSONResponse(list_cuts())


@app.post("/api/cuts/copy")
async def api_cuts_copy(request: Request) -> JSONResponse:
    """Save a copy of the cut on the board under a name — and, unless told otherwise,
    work on the copy from here on. `open: false` is the other use: a checkpoint you
    can come back to while you keep editing what you have."""
    body = await request.json()
    name = _cut_name_from(body)
    open_it = bool(body.get("open", True)) if isinstance(body, dict) else True
    listing = list_cuts()
    if name.lower() in {r["name"].lower() for r in listing["cuts"]}:
        raise HTTPException(409, f"there is already a cut called {name!r}")
    dest = cuts_dir() / f"{cut_slug(name)}{CUT_SUFFIX}"
    if dest.exists():
        raise HTTPException(409, f"{dest.name} already exists")
    if open_it:
        _refuse_while_running("switching cuts")
    edl = read_edl()
    edl["cut"] = {"name": name, "created": time.time(),
                  "from": cut_name_of(edl, STATE["edl"])}
    _write_cut(dest, edl)
    if open_it:
        switch_cut(dest)
    out = list_cuts()
    out["copy"] = next(r for r in out["cuts"] if Path(r["path"]) == dest)
    return JSONResponse(out)


@app.post("/api/cuts/open")
async def api_cuts_open(request: Request) -> JSONResponse:
    body = await request.json()
    path = _known_cut(body.get("path") if isinstance(body, dict) else None)
    if path != STATE["edl"]:
        _refuse_while_running("switching cuts")
        switch_cut(path)
    return JSONResponse(list_cuts())


@app.post("/api/cuts/rename")
async def api_cuts_rename(request: Request) -> JSONResponse:
    """The name lives inside the file, so renaming never moves anything and nothing
    that points at the file (the registry, a render's metadata) goes stale."""
    body = await request.json()
    path = _known_cut(body.get("path") if isinstance(body, dict) else None)
    name = _cut_name_from(body)
    others = {r["name"].lower() for r in list_cuts()["cuts"] if Path(r["path"]) != path}
    if name.lower() in others:
        raise HTTPException(409, f"there is already a cut called {name!r}")
    try:
        edl = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(500, f"could not read {path.name}: {exc}")
    meta = edl.get("cut") if isinstance(edl.get("cut"), dict) else {}
    meta["name"] = name
    edl["cut"] = meta
    _write_cut(path, edl)
    return JSONResponse(list_cuts())


@app.post("/api/cuts/delete")
async def api_cuts_delete(request: Request) -> JSONResponse:
    """Never the cut on the board, and never actually gone: the file moves to the
    bin's `trash/` with a timestamp, where a hand can get it back."""
    body = await request.json()
    path = _known_cut(body.get("path") if isinstance(body, dict) else None)
    if path == STATE["edl"]:
        raise HTTPException(409, "that is the cut on the board — open another one first")
    trash = cuts_dir() / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    stem = path.name[:-len(CUT_SUFFIX)] if path.name.endswith(CUT_SUFFIX) else path.stem
    dest = trash / f"{stem}.{int(time.time())}{CUT_SUFFIX}"
    try:
        path.replace(dest)
    except OSError:                      # a --edl cut on another filesystem
        shutil.move(str(path), str(dest))
    known = _registry()
    rec = known.get(STATE["footage"].name)
    if isinstance(rec, dict) and rec.get("cuts"):
        rec["cuts"] = [c for c in rec["cuts"] if Path(c) != path]
        _write_registry(known)
    return JSONResponse({**list_cuts(), "trashed": str(dest)})


PROJECTS_FILE = "projects.json"       # under --work: {name: {footage, edl, opened, cuts}}


def projects_path() -> Path:
    return STATE["work"] / PROJECTS_FILE


def remember_project(footage: Path, edl_path: Path) -> None:
    """Every bin the board has been pointed at, so the picker can offer it again. The
    EDL does not carry its footage path (it never needed to — the folder was the
    argument), so this small registry does."""
    known = _registry()
    rec = known.get(footage.name) if isinstance(known.get(footage.name), dict) else {}
    # `edl` is the cut the bin was last open on; `cuts` remembers the ones that live
    # outside `projects/` (named with --edl), which nothing else would list.
    extra = [c for c in (rec.get("cuts") or []) if isinstance(c, str)]
    under = STATE["work"] / "projects"
    if edl_path != default_cut_path(footage.name) and edl_path.parent != cuts_dir(footage.name) \
            and str(edl_path) not in extra and under not in edl_path.parents:
        extra.append(str(edl_path))
    known[footage.name] = {"footage": str(footage), "edl": str(edl_path),
                           "opened": time.time(), "cuts": extra}
    _write_registry(known)


def _write_registry(known: dict) -> None:
    projects_path().parent.mkdir(parents=True, exist_ok=True)
    tmp = projects_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(known, indent=1), encoding="utf-8")
    tmp.replace(projects_path())


def _count_videos(folder: Path) -> int:
    try:
        return sum(1 for q in folder.iterdir() if q.suffix.lower() in VIDEO_SUFFIXES)
    except OSError:
        return 0


def list_projects() -> dict:
    """The bins this board knows: every one it has opened (the registry) and every
    folder of video next to the current one (a trip's footage usually sits in one
    parent, one folder per bin). Facts only — clip counts, whether it has been cut,
    whether it has a journal — never a judgement."""
    current: Path = STATE["footage"]
    known = _registry()
    # Keyed by the folder's real path: a bin reached by two names (~/footage is a
    # symlink to /mnt/roughcut/footage on foxtrot) is one bin, listed once — the
    # most recently opened record of it.
    rows: dict[str, dict] = {}
    for name, rec in known.items():
        if not isinstance(rec, dict) or not rec.get("footage"):
            continue
        folder = Path(rec["footage"])
        key = os.path.realpath(folder)
        if key in rows and (rows[key]["opened"] or 0) >= (rec.get("opened") or 0):
            continue
        rows[key] = {"name": name, "footage": str(folder), "known": True,
                     "opened": rec.get("opened"), "_edl": rec.get("edl")}
    for folder in sorted(current.parent.iterdir()) if current.parent.is_dir() else []:
        if folder.is_dir() and os.path.realpath(folder) not in rows and _count_videos(folder):
            rows[os.path.realpath(folder)] = {"name": folder.name, "footage": str(folder),
                                              "known": False, "opened": None, "_edl": None}
    out = []
    for key, r in rows.items():
        folder = Path(r["footage"])
        here = key == os.path.realpath(current)
        if here:
            folder = current
        remembered = r.pop("_edl")
        # The cut this bin would open on: the one it was last on if it still exists,
        # else its own file; the row reports that cut's shots and how many cuts the
        # bin has, so the picker can say "cut · 21 shots · 3 cuts".
        edl_path = default_cut_path(folder.name)
        if here:
            edl_path = STATE["edl"]
        elif remembered and Path(remembered).is_file():
            edl_path = Path(remembered)
        segments, cut_name = 0, None
        if edl_path.exists():
            try:
                edl = json.loads(edl_path.read_text(encoding="utf-8"))
                segments = len(edl.get("segments") or [])
                cut_name = cut_name_of(edl, edl_path, folder.name)
            except (OSError, ValueError):
                segments = 0
        n_cuts = len(_cut_paths(folder.name))
        if here and STATE["edl"] not in _cut_paths(folder.name):
            n_cuts += 1
        clips = (len(footage_clips()) if here
                 else _count_videos(folder) + len(mediamod.Sources(STATE["work"],
                                                                   folder.name).present()))
        out.append({**r, "exists": folder.is_dir(), "clips": clips,
                    "cut": edl_path.exists(), "segments": segments,
                    "cut_name": cut_name, "cuts": n_cuts,
                    "journal": (STATE["work"] / "index" / f"{folder.name}.json").exists(),
                    "current": here})
    out.sort(key=lambda r: (not r["current"], -(r["opened"] or 0), r["name"]))
    return {"current": {"name": current.name, "footage": str(current),
                        "edl": str(STATE["edl"]), "clips": len(footage_clips()),
                        "cut": cut_name_of(read_edl(), STATE["edl"])},
            "projects": out}


@app.get("/api/projects")
def api_projects() -> JSONResponse:
    return JSONResponse(list_projects())


@app.post("/api/projects/open")
async def api_projects_open(request: Request) -> JSONResponse:
    """Point the board at another bin without a relaunch (INTAKE I5.4). Refused while
    any job is running: a render or an index mid-flight belongs to the bin it started
    on. Everything per-bin is re-derived by `configure()`, the same path `main()` takes."""
    body = await request.json()
    raw = body.get("footage") if isinstance(body, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(400, "footage: a folder path is required")
    folder = Path(raw).expanduser()
    if not folder.is_dir():
        raise HTTPException(400, f"no such folder: {folder}")
    if not _count_videos(folder):
        raise HTTPException(400, f"no video in {folder}")
    _refuse_while_running("switching bins")
    folder = folder.resolve()
    # Which cut to land on: the one asked for (it must be one of the bin's), else the
    # one the bin was last open on, else the bin's own file — scaffolded if new.
    raw_edl = body.get("edl")
    edl: Path | None = None
    if isinstance(raw_edl, str) and raw_edl.strip():
        edl = Path(raw_edl).expanduser()
        if edl not in _cut_paths(folder.name):
            raise HTTPException(400, f"not a cut of {folder.name}: {raw_edl}")
    else:
        rec = _registry().get(folder.name)
        if isinstance(rec, dict) and rec.get("edl") and Path(rec["edl"]).is_file() \
                and Path(rec.get("footage", "")) == folder:
            edl = Path(rec["edl"])
    for reg in (ANALYSES, VISUALS, INDEXES, ASKS, FINDS, THEMES, RENDERS, DEEPS, IMPORTS):
        reg.clear()
    configure(edl, folder, None, STATE["work"],
              proxies=STATE.get("proxies_enabled", True), orient="auto")
    return JSONResponse({**list_projects()["current"],
                         "edl_created": STATE["edl_created"]})


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
        # PROXY_V: a proxy from an older build_proxy is rebuilt, never served.
        "proxy_dir": work / "proxies" / footage.name / PROXY_V,
        # One JPEG per shot card, cut out of the proxy at the shot's in-point. Per-bin
        # like the proxies they come from, and disposable: every one of them is a
        # seek and a single frame, rebuilt on demand when the folder is not there.
        "posters": work / "posters" / footage.name,
        # Per-clip colour files (INTAKE M10): per bin, because colour is per project.
        "colour_dir": work / "colour" / footage.name,
        # Per-bin for the same reason as proxies, and because a fresh project that
        # opens claiming "1 version" and plays another trip's cut in the A slot is
        # worse than showing nothing.
        "renders": work / "renders" / footage.name,
        # 720p copies of the finished renders, for the A/B players. Per-bin like the
        # renders they come from, and as disposable as the posters: deleting the
        # folder costs one re-encode per version and nothing else.
        "reviews": work / "reviews" / footage.name,
        "proxies_ready": False, "edl_created": created,
        "probes": {},                        # clip_probe's cache: per bin, like the rest
        # What was imported or uploaded into this bin (wave 12 IMPORT, roughcut/media.py):
        # work/media/<bin>/sources.json, read here once and kept in memory
        "media_sources": mediamod.Sources(work, footage.name),
    })
    STATE["sidecars"].mkdir(parents=True, exist_ok=True)
    if created:
        scaffold_edl(edl_path, footage, orient)
    STATE["orient"] = read_edl().get("orient", "auto")   # needs STATE["edl"] set first
    # A cut written before shots had ids gets them once, here at open — the one write a
    # read path never makes (INTAKE M9 I9.0).
    _edl = read_edl()
    if ensure_segment_ids(_edl):
        write_edl(_edl)
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
    # Per-clip caches are keyed by clip name, and two bins can share a GoPro stem: a
    # re-point must not serve the last bin's durations or capture times for this one.
    for key in ("capture", "durations", "telemetry", "render_res_cache", "clip_facts"):
        STATE.pop(key, None)
    # the status snapshots and the peaks in memory are this bin's (wave 11 CHEAP)
    forget_peaks()
    with SNAP["lock"]:
        SNAP["cache"].clear()
        SNAP["bytes"].clear()
        SNAP["rearm"] = True
        SNAP["gen"] += 1
    # The Project panel's first /api/clips finds the free facts probed or probing
    # (redesign wave 2, INGEST): the probes start now, in the background.
    warm_clip_facts(footage_clips())
    STATE["proxies_enabled"] = proxies
    remember_project(footage, edl_path)
    STATE["asks"].mkdir(parents=True, exist_ok=True)

    # the heard clips, by their real names (an imported .mov is not `<stem>.MP4`)
    heard = analysed_stems()
    clips = [c for c in footage_clips() if Path(c).stem in heard]
    if not proxies:
        STATE["proxies_ready"] = True
    else:
        threading.Thread(target=ensure_proxies, args=(clips,), daemon=True).start()
        kick_previews()          # posters and playable previews for every clip, first


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
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
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to listen on (default: this machine only). The "
                         "board has no login and serves the footage, so 0.0.0.0 — "
                         "every network the machine is on — is opt-in, e.g. when "
                         "WSL's localhost forwarding breaks and the board is "
                         "reached at the WSL IP")
    ap.add_argument("--no-proxies", action="store_true")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the one-call backend auth check at startup")
    return ap.parse_args(argv)


def main() -> int:
    args = parse_args()

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

    STATE["cli_facts"] = not args.no_probe
    pre = backend_preflight()
    print(f"backend: {pre['backend']} · deep {pre['models']['deep']} · "
          f"quick {pre['models']['quick']}", flush=True)
    if pre["cli"]:
        print(f"  claude CLI {pre['cli'].get('version')} · "
              f"signed in: {pre['cli'].get('logged_in')}", flush=True)
    if pre["fix"]:
        print(f"  !! {pre['fix']['title']} — run: {pre['fix']['command']}", flush=True)
    for problem in pre["problems"]:
        print(f"  !! {problem}", flush=True)
    if not args.no_probe and not pre["problems"]:
        probe_backend()          # one call, in the background; result shows in the UI

    print(f"cut board on http://localhost:{args.port}  (edl: {STATE['edl'].name})", flush=True)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"  !! listening on {args.host}: anyone who can reach this machine can "
              f"open the board (no login)", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
