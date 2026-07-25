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

Usage:
    uv run app/server.py --edl research/edl/B1-variantB.json \
        --footage ~/footage/copper-02-2026 --sidecars ~/work/audio
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
import uvicorn

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "research" / "tools"
PROXY_W = 1280
PROXY_CRF = 26

app = FastAPI()
STATE: dict = {}
RENDERS: dict[str, dict] = {}


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


def configure(edl: Path, footage: Path, sidecars: Path, work: Path,
              proxies: bool = True) -> None:
    """Point the app at a project. Shared by main() and the test suite, so tests
    exercise the same wiring the server uses rather than a parallel setup."""
    work = work.expanduser().resolve()
    STATE.update({
        "edl": edl.resolve(), "footage": footage.expanduser().resolve(),
        "sidecars": sidecars.expanduser().resolve(),
        "work": work, "proxy_dir": work / "proxies", "renders": work / "renders",
        "proxies_ready": False,
    })
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
    ap.add_argument("--edl", type=Path, required=True)
    ap.add_argument("--footage", type=Path, required=True)
    ap.add_argument("--sidecars", type=Path, required=True)
    ap.add_argument("--work", type=Path, default=Path.home() / "work" / "app")
    ap.add_argument("--port", type=int, default=87 * 100 + 65)   # 8765
    ap.add_argument("--no-proxies", action="store_true")
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe", "uv"):
        if shutil.which(tool) is None:
            raise SystemExit(f"error: {tool} not found on PATH")

    configure(args.edl, args.footage, args.sidecars, args.work,
              proxies=not args.no_proxies)
    print(f"cut board on http://localhost:{args.port}  (edl: {STATE['edl'].name})")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
