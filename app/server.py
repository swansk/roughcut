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
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roughcut import inference, revise          # noqa: E402  (after sys.path)

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


# ---------------------------------------------------------------- proxies


def build_proxy(src: Path, dest: Path, orient: str) -> None:
    # Written to a temp name and renamed, because the server is already serving while
    # this thread runs: a half-written file at the final path is handed to a <video>
    # element as a truncated stream, which fails to decode and is then cached as
    # broken until a reload. os.replace is atomic within a filesystem.
    tmp = dest.with_suffix(".part.mp4")
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


def ensure_proxies(clips: list[str]) -> None:
    pdir: Path = STATE["proxy_dir"]
    pdir.mkdir(parents=True, exist_ok=True)
    todo = [c for c in clips if not (pdir / f"{Path(c).stem}.mp4").exists()]
    if todo:
        print(f"building {len(todo)} proxies (once per clip)...")
    for i, clip in enumerate(todo, 1):
        src = STATE["footage"] / clip
        if not src.exists():
            print(f"  !! missing footage {clip}")
            continue
        build_proxy(src, pdir / f"{Path(clip).stem}.mp4", STATE["orient"])
        print(f"  [{i}/{len(todo)}] {clip}")
    STATE["proxies_ready"] = True
    print("proxies ready")


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
        "tools": {t: shutil.which(t) is not None
                  for t in ("ffmpeg", "ffprobe", "uv")},
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
    r = subprocess.run(
        ["uv", "run", "--quiet", str(TOOLS / "edl_snap.py"), str(tmp_in),
         "--sidecars", str(STATE["sidecars"]), "-o", str(tmp_out)],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise HTTPException(500, f"snap failed: {r.stderr[-300:]}")
    return JSONResponse({"segments": json.loads(tmp_out.read_text())["segments"],
                         "log": r.stdout})


@app.post("/api/ask")
async def api_ask(request: Request) -> JSONResponse:
    """Plain-language note in, revised timeline out — as a proposal, never a write.

    The one thing the board could not do before: let the human steer by *asking*
    rather than by dragging. Returned like /api/snap so the UI can show it, keep it
    undoable, and let the human reject it — a revision that applied itself would be
    the opposite of the "fun and easy" this app exists for.
    """
    body = await request.json()
    note = (body.get("note") or "").strip()
    if not note:
        raise HTTPException(400, "empty note")

    payload = project_payload()
    clips = {c: {"clip": c, "duration": v["duration"], "transcript": v["transcript"],
                 "summary": v["summary"]} for c, v in payload["clips"].items()}
    target = payload["target"]
    try:
        plan = revise.propose(
            segments=body.get("segments") or payload["segments"],
            clips=clips, story=body.get("story", payload.get("story", "")),
            note=note, target=(float(target[0]), float(target[1])))
    except inference.BudgetExceeded as exc:
        raise HTTPException(429, str(exc)) from exc
    except (inference.InferenceError, ValueError) as exc:
        raise HTTPException(502, str(exc)) from exc
    return JSONResponse(plan)


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


def _analyze_job(job: str, cmd: list[str], wanted: set[str]) -> None:
    entry = ANALYSES[job]
    lines: list[str] = []
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
    except OSError as exc:
        entry.update(state="failed", log=str(exc))
        return
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line.rstrip())
        # Progress is counted from the sidecars on disk rather than parsed out of the
        # tool's chatter: audio_analyze.py writes each one as it finishes, so the
        # filesystem is the honest progress bar and stays right if the log format moves.
        entry["done"] = len(wanted & analysed_stems())
        entry["log"] = "\n".join(lines[-40:])
    rc = proc.wait()
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
        entry["stage"] = "previews"
        STATE["proxies_ready"] = False
        ensure_proxies(sorted(c for c in footage_clips()
                              if Path(c).stem in analysed_stems()))
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
                     "total": len(wanted), "done": len(wanted & analysed_stems())}
    threading.Thread(target=_analyze_job,
                     args=(job, analyze_cmd(skip, force), wanted),
                     daemon=True).start()
    return JSONResponse({"job": job, "total": len(wanted)})


@app.get("/api/analyze/{job}")
def api_analyze_status(job: str) -> JSONResponse:
    if job not in ANALYSES:
        raise HTTPException(404, "no such job")
    return JSONResponse(ANALYSES[job])


def _render_job(job: str, edl_path: Path, out_path: Path) -> None:
    cmd = ["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
           "--footage", str(STATE["footage"]), "--sidecars", str(STATE["sidecars"]),
           "-o", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    RENDERS[job] = {
        "state": "done" if r.returncode == 0 else "failed",
        "log": (r.stdout or "") + (r.stderr or ""),
        "output": str(out_path) if r.returncode == 0 else None,
        "url": f"/media/render/{out_path.name}" if r.returncode == 0 else None,
    }


@app.post("/api/render")
async def api_render(request: Request) -> JSONResponse:
    body = await request.json()
    edl = read_edl()
    edl["segments"] = body["segments"]
    job = uuid.uuid4().hex[:8]
    edl_path = STATE["work"] / f"render_{job}.json"
    edl_path.write_text(json.dumps(edl, indent=1), encoding="utf-8")
    out_path = STATE["renders"] / f"cut_{job}.mp4"
    RENDERS[job] = {"state": "running", "log": "", "output": None, "url": None}
    threading.Thread(target=_render_job, args=(job, edl_path, out_path),
                     daemon=True).start()
    return JSONResponse({"job": job})


@app.get("/api/render/{job}")
def api_render_status(job: str) -> JSONResponse:
    if job not in RENDERS:
        raise HTTPException(404, "no such job")
    return JSONResponse(RENDERS[job])


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
        "renders": work / "renders",
        "proxies_ready": False, "edl_created": created,
    })
    STATE["sidecars"].mkdir(parents=True, exist_ok=True)
    if created:
        scaffold_edl(edl_path, footage, orient)
    STATE["orient"] = read_edl().get("orient", "auto")   # needs STATE["edl"] set first
    STATE["work"].mkdir(parents=True, exist_ok=True)
    STATE["renders"].mkdir(parents=True, exist_ok=True)
    STATE["proxy_dir"].mkdir(parents=True, exist_ok=True)

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
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe", "uv"):
        if shutil.which(tool) is None:
            raise SystemExit(f"error: {tool} not found on PATH")
    if not args.footage.expanduser().is_dir():
        raise SystemExit(f"error: no such footage folder: {args.footage}")

    configure(args.edl, args.footage, args.sidecars, args.work,
              proxies=not args.no_proxies, orient=args.orient)
    clips, done = footage_clips(), analysed_stems()
    print(f"{STATE['footage']}: {len(clips)} clips, "
          f"{sum(1 for c in clips if Path(c).stem in done)} analysed")
    if STATE["edl_created"]:
        print(f"new project: {STATE['edl']}")
    print(f"cut board on http://localhost:{args.port}  (edl: {STATE['edl'].name})")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
