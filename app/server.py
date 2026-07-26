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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roughcut import config, inference, revise   # noqa: E402  (after sys.path)

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "research" / "tools"
PROXY_W = 1280
PROXY_CRF = 26
# Kept in step with audio_analyze.py — the two must agree on what counts as footage,
# or the "N clips, M analysed" the UI shows would never reach parity.
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".webm"}

app = FastAPI()
STATE: dict = {}
RENDERS: dict[str, dict] = {}
ANALYSES: dict[str, dict] = {}
ASKS: dict[str, dict] = {}


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


def read_edl() -> dict:
    return json.loads(STATE["edl"].read_text(encoding="utf-8"))


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
            "transcript": d.get("transcript", []),
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
        "proxies_ready": STATE.get("proxies_ready", False),
        "edl_path": str(STATE["edl"]),
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
    STATE["edl"].write_text(json.dumps(edl, indent=1), encoding="utf-8")
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


def _ask_job(job: str, segments: list[dict], clips: dict, story: str, note: str,
             target: tuple[float, float]) -> None:
    entry = ASKS[job]
    try:
        if segments:
            plan = revise.propose(segments=segments, clips=clips, story=story,
                                  note=note, target=target)
        else:
            plan = revise.originate(clips=clips, story=story, note=note,
                                    target=target)
    except inference.BudgetExceeded as exc:
        entry.update(state="failed", detail=str(exc), code=429)
        return
    except (inference.InferenceError, ValueError) as exc:
        entry.update(state="failed", detail=str(exc), code=502)
        return
    # On disk before it is announced. A two-minute call whose only copy is an HTTP
    # response is one dropped connection away from being spent for nothing — which is
    # exactly what happened on the first Killington ask: the model answered, the
    # browser never showed it, and the plan was only recoverable from the CLI's own
    # session transcript.
    record = {"job": job, "created": time.time(), "note": note, "story": story,
              "plan": plan}
    path = STATE["asks"] / f"{job}.json"
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    entry.update(state="done", plan=plan)


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

    payload = project_payload()
    clips = {c: {"clip": c, "duration": v["duration"], "transcript": v["transcript"],
                 "summary": v["summary"], "captured": v.get("captured")}
             for c, v in payload["clips"].items()}
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

    job = uuid.uuid4().hex[:8]
    ASKS[job] = {"state": "running", "started": time.time(), "plan": None,
                 "detail": "", "code": 0,
                 "kind": "revision" if segments else "first cut"}
    threading.Thread(
        target=_ask_job,
        args=(job, segments, clips, story, note,
              (float(target[0]), float(target[1]))), daemon=True).start()
    return JSONResponse({"job": job})


@app.get("/api/ask/{job}")
def api_ask_status(job: str) -> JSONResponse:
    if job not in ASKS:
        raise HTTPException(404, "no such job")
    entry = ASKS[job]
    return JSONResponse({**entry,
                         "elapsed_s": round(time.time() - entry["started"], 1)})


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


def _analyze_job(job: str, cmd: list[str], wanted: set[str]) -> None:
    entry = ANALYSES[job]
    lines: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            bufsize=1,
            # So the log streams too, rather than arriving in one lump at exit.
            env={**os.environ, "PYTHONUNBUFFERED": "1"})
    except OSError as exc:
        entry.update(state="failed", log=str(exc))
        return
    assert proc.stdout is not None

    # Counted from the sidecars on disk rather than parsed out of the tool's chatter:
    # audio_analyze.py writes each one as it finishes, so the filesystem is the honest
    # progress bar and stays right if the log format moves.
    stop = threading.Event()
    ticker = threading.Thread(
        target=_ticker,
        args=(stop, lambda: entry.__setitem__("done", len(wanted & analysed_stems()))),
        daemon=True)
    ticker.start()
    try:
        for line in proc.stdout:
            lines.append(line.rstrip())
            entry["log"] = "\n".join(lines[-40:])
        rc = proc.wait()
    finally:
        stop.set()
    entry["done"] = len(wanted & analysed_stems())
    entry["log"] = "\n".join(lines[-40:])
    if rc != 0:
        entry["state"] = "failed"
        return
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
        ensure_proxies(
            sorted(c for c in footage_clips() if Path(c).stem in analysed_stems()),
            progress=lambda done, total: entry.update(proxy_done=done,
                                                      proxy_total=total))
    entry["stage"] = "done"
    entry["state"] = "done"


@app.post("/api/analyze")
async def api_analyze(request: Request) -> JSONResponse:
    """Run the audio pass over the bin, in-app.

    This was step 2 of five terminal steps standing between a folder of footage and
    the board. It is the cheapest of them to move inside — the tool already writes one
    sidecar per clip as it goes, so progress is real rather than a spinner.
    """
    body = await request.json()
    if any(a["state"] == "running" for a in ANALYSES.values()):
        raise HTTPException(409, "an analysis is already running")
    skip = [str(s).strip() for s in body.get("skip", []) if str(s).strip()]
    force = bool(body.get("force"))
    skipped = {s.upper() for s in skip}
    wanted = {Path(c).stem for c in footage_clips()
              if Path(c).stem.upper() not in skipped}
    if not wanted:
        raise HTTPException(400, "no clips to analyse")

    job = uuid.uuid4().hex[:8]
    ANALYSES[job] = {"state": "running", "stage": "analysing", "log": "",
                     "total": len(wanted), "done": len(wanted & analysed_stems()),
                     "proxy_done": 0, "proxy_total": 0}
    threading.Thread(target=_analyze_job,
                     args=(job, analyze_cmd(skip, force), wanted),
                     daemon=True).start()
    return JSONResponse({"job": job, "total": len(wanted)})


@app.get("/api/analyze/{job}")
def api_analyze_status(job: str) -> JSONResponse:
    if job not in ANALYSES:
        raise HTTPException(404, "no such job")
    return JSONResponse(ANALYSES[job])


def probe_duration(path: Path) -> float | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return round(float(r.stdout.strip().strip(",")), 2)
    except ValueError:
        return None


def _render_job(job: str, edl_path: Path, out_path: Path, meta: dict) -> None:
    entry = RENDERS[job]
    parts_dir = STATE["work"] / f"parts_{job}"
    cmd = ["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
           "--footage", str(STATE["footage"]), "--sidecars", str(STATE["sidecars"]),
           "--parts-dir", str(parts_dir), "-o", str(out_path)]

    # Same shape as the audio pass: a ticker counting finished parts on disk, so the
    # UI can say "cutting 7/16" instead of "rendering…" for two minutes. Karl, on
    # clicking Render: "got like no response - and just see rendering..."
    stop = threading.Event()

    def count() -> None:
        done = len(list(parts_dir.glob("part_*.mp4"))) if parts_dir.exists() else 0
        entry["done"] = done
        entry["stage"] = "joining" if done >= entry["total"] else "cutting"

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
        out_path.with_suffix(".json").write_text(json.dumps(meta, indent=1),
                                                 encoding="utf-8")
    entry.update(
        state="done" if ok else "failed",
        stage="done" if ok else "failed",
        done=entry["total"] if ok else entry["done"],
        log=(r.stdout or "") + (r.stderr or ""),
        output=str(out_path) if ok else None,
        url=f"/media/render/{out_path.name}" if ok else None,
    )


@app.post("/api/render")
async def api_render(request: Request) -> JSONResponse:
    body = await request.json()
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
    }
    RENDERS[job] = {"state": "running", "stage": "cutting", "log": "",
                    "output": None, "url": None, "started": time.time(),
                    "done": 0, "total": len(edl["segments"])}
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
    for mp4 in STATE["renders"].glob("cut_*.mp4"):
        meta_path = mp4.with_suffix(".json")
        meta = (json.loads(meta_path.read_text(encoding="utf-8"))
                if meta_path.exists() else {})
        out.append({
            "name": mp4.name, "url": f"/media/render/{mp4.name}",
            "size": mp4.stat().st_size,
            "created": meta.get("created", mp4.stat().st_mtime),
            "duration_s": meta.get("duration_s"), "segments": meta.get("segments"),
            "planned_s": meta.get("planned_s"), "note": meta.get("note", ""),
        })
    out.sort(key=lambda r: r["created"], reverse=True)
    return JSONResponse({"renders": out})


@app.get("/api/render/{job}")
def api_render_status(job: str) -> JSONResponse:
    if job not in RENDERS:
        raise HTTPException(404, "no such job")
    entry = RENDERS[job]
    return JSONResponse({
        **entry,
        "elapsed_s": round(time.time() - entry.get("started", time.time()), 1)})


# ---------------------------------------------------------------- media


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
    m = re.match(r"bytes=(\d*)-(\d*)", rng)
    if not m:
        raise HTTPException(416, "bad range")
    start = int(m.group(1)) if m.group(1) else 0
    end = int(m.group(2)) if m.group(2) else size - 1
    end = min(end, size - 1)
    if start > end:
        raise HTTPException(416, "bad range")
    with path.open("rb") as fh:
        fh.seek(start)
        data = fh.read(end - start + 1)
    return Response(data, status_code=206, media_type=mime, headers={
        "content-range": f"bytes {start}-{end}/{size}",
        "accept-ranges": "bytes",
        "content-length": str(len(data)),
    })


@app.get("/media/proxy/{name}")
def media_proxy(name: str, request: Request) -> Response:
    return ranged_file(STATE["proxy_dir"] / Path(name).name, request)


@app.get("/media/render/{name}")
def media_render(name: str, request: Request) -> Response:
    return ranged_file(STATE["renders"] / Path(name).name, request)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((HERE / "static" / "index.html").read_text(encoding="utf-8"))


@app.get("/app.js")
def appjs() -> Response:
    return Response((HERE / "static" / "app.js").read_text(encoding="utf-8"),
                    media_type="application/javascript")


def configure(edl: Path | None, footage: Path, sidecars: Path | None, work: Path,
              proxies: bool = True, orient: str = "auto") -> None:
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
        # Per-bin for the same reason as proxies, and because a fresh project that
        # opens claiming "1 version" and plays another trip's cut in the A slot is
        # worse than showing nothing.
        "renders": work / "renders" / footage.name,
        "proxies_ready": False, "edl_created": created,
    })
    STATE["sidecars"].mkdir(parents=True, exist_ok=True)
    if created:
        scaffold_edl(edl_path, footage, orient)
    STATE["orient"] = read_edl().get("orient", "auto")   # needs STATE["edl"] set first
    STATE["asks"] = work / "asks" / footage.name
    STATE["work"].mkdir(parents=True, exist_ok=True)
    STATE["renders"].mkdir(parents=True, exist_ok=True)
    STATE["proxy_dir"].mkdir(parents=True, exist_ok=True)
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
              proxies=not args.no_proxies, orient=args.orient)
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
