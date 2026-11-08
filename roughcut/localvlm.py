"""The local GPU as an index stage (wave 20, lane LOCALGPU): the app's side.

The heavy half lives in `localgpu/` (a separate uv project: torch cu128, transformers,
bitsandbytes, open_clip, PyNvVideoCodec) and is only ever run as a subprocess, so this
module imports nothing heavy and the suite passes on a CPU-only machine. Install it with
`scripts/install-local-models.sh`.

Public API — every call degrades to "not available" instead of raising:

    available()                      -> {"ok": bool, "why": str, ...}
    check_window(clip, start, end, questions=("fall",))
                                     -> {question: P(yes) | None}     (VERIFY's local half)
    check_window_ex(...)             -> {"ok", "why", "p_yes": {...}, "detail", "frames"}
    embed_clip(clip)                 -> {"ok", "why", "n", ...}       (the stage does this per clip)
    search(text, k=3, clips=None)    -> [{clip, start, end, score}]   ([] when not available)
    search_ex(...)                   -> {"ok", "why", "results": {text: [...]}}
    sidecar(clip)                    -> the clip's `<stem>.local.json` dict, or None
    checks(clip)                     -> its list of checks (see below), [] when none

A check (one entry of a sidecar's "checks"):
    {"t", "cand": {kind, start, end, t, ...telemetry numbers}, "q": "fall"|"air"|...,
     "p_yes", "verdict": "likely"|"unlikely"|"advisory"|"unchecked", "threshold",
     "settles": bool, "frames": {t0, t1, n, fps, levelled, sharp_of},
     "scan_peak_t"?, "scan_peak_p"?, "gpu_s"}
`p_yes >= 0.2` on a fall question is "likely" (the bake-off cut; the 0.2-0.9 band is for Claude
to settle); an air question is only ever "advisory" — **an air is never settled locally**.

Context: the server binds the bin (`bind(footage, work, tools)`); every path below hangs off
`<work>/local/<bin>/`. `ROUGHCUT_LOCALGPU=0` turns the whole thing off (the test suite does).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

REPO = Path(__file__).resolve().parent.parent
LOCALGPU_DIR = REPO / "localgpu"
NICE = 5                              # GPU jobs yield to the editor's own work
FALL_THRESHOLD = 0.2                  # kept in step with localgpu/rcgpu/config.py
_AVAIL_TTL_S = 30.0


# ------------------------------------------------------------------ the install

def enabled() -> bool:
    return os.environ.get("ROUGHCUT_LOCALGPU", "1").strip().lower() not in ("0", "off", "no", "false")


def venv_dir() -> Path:
    env = os.environ.get("ROUGHCUT_LOCALGPU_VENV")
    return Path(env) if env else Path.home() / ".cache" / "roughcut" / "localgpu-venv"


def venv_python() -> Path:
    return venv_dir() / "bin" / "python"


def models_dir() -> Path:
    env = os.environ.get("ROUGHCUT_MODELS")
    return Path(env) if env else Path.home() / ".cache" / "roughcut" / "models"


def _gpu_visible() -> tuple[bool, str]:
    cvd = os.environ.get("CUDA_VISIBLE_DEVICES")
    if cvd is not None and cvd.strip() in ("", "-1", "NoDevFiles"):
        return False, "no GPU is visible (CUDA_VISIBLE_DEVICES hides it)"
    smi = shutil.which("nvidia-smi")
    if not smi:
        return False, "no NVIDIA GPU (nvidia-smi not found)"
    try:
        r = subprocess.run([smi, "-L"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False, "nvidia-smi did not answer"
    line = (r.stdout or "").strip().splitlines()
    if r.returncode != 0 or not line:
        return False, "no NVIDIA GPU found"
    return True, line[0].split(" (UUID")[0]


_AVAIL: dict = {"at": 0.0, "val": None, "key": None}


def available(refresh: bool = False, deep: bool = False) -> dict:
    """Is the local stack usable here? {"ok", "why", ...}. Cheap by default (files + nvidia-smi,
    cached 30 s); `deep=True` runs `rcgpu status` in the environment (imports torch, ~10 s)."""
    if not enabled():
        return {"ok": False, "why": "the local GPU stage is switched off (ROUGHCUT_LOCALGPU=0)"}
    key = (os.environ.get("CUDA_VISIBLE_DEVICES"), str(venv_dir()), str(models_dir()), deep)
    if not refresh and _AVAIL["val"] is not None and _AVAIL["key"] == key \
            and time.time() - _AVAIL["at"] < _AVAIL_TTL_S:
        return _AVAIL["val"]
    out = _probe(deep)
    _AVAIL.update(at=time.time(), val=out, key=key)
    return out


def _probe(deep: bool) -> dict:
    ok, gpu = _gpu_visible()
    if not ok:
        return {"ok": False, "why": gpu}
    if not venv_python().exists():
        return {"ok": False, "why": "the local stack is not installed (scripts/install-local-models.sh)",
                "gpu": gpu}
    miss = []
    if not (models_dir() / "qwen35-4b-bnb4").is_dir():
        miss.append("Qwen3.5-4B")
    hub = models_dir() / "hf" / "hub" / "models--timm--PE-Core-L-14-336" / "snapshots"
    if not (hub.is_dir() and any(hub.iterdir())):
        miss.append("PE-Core")
    if miss:
        return {"ok": False, "why": f"models missing: {', '.join(miss)} (scripts/install-local-models.sh)",
                "gpu": gpu}
    out = {"ok": True, "why": "", "gpu": gpu}
    if deep:
        try:
            r = subprocess.run(_cmd("status"), capture_output=True, text=True, timeout=120, env=_env())
            info = json.loads((r.stdout or "{}").strip().splitlines()[-1])
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "why": f"status probe failed: {exc}"[:200], "gpu": gpu}
        out.update(status=info)
        if not info.get("ok"):
            out.update(ok=False, why=str(info.get("error") or "the environment cannot reach CUDA / NVDEC")[:200])
    return out


def _cmd(*args: str, nice: bool = True) -> list[str]:
    base = [str(venv_python()), "-m", "rcgpu", *args]
    return (["nice", "-n", str(NICE)] + base) if nice else base


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(LOCALGPU_DIR), str(REPO), env.get("PYTHONPATH", "")]))
    env["ROUGHCUT_MODELS"] = str(models_dir())
    env["PYTHONUNBUFFERED"] = "1"
    return env


# ------------------------------------------------------------------ the bin

@dataclass
class Context:
    footage: Path          # the folder the clips are in
    work: Path             # --work
    tools: Path            # research/tools (the GPMF parser)
    bin: str

    @property
    def dir(self) -> Path:
        return self.work / "local" / self.bin

    def video(self, clip: str) -> Path:
        return self.footage / clip

    def telemetry_dir(self) -> Path:
        return self.work / "telemetry" / self.bin


CTX: Context | None = None


def bind(footage: Path, work: Path, tools: Path | None = None) -> Context:
    global CTX
    CTX = Context(Path(footage), Path(work), Path(tools or REPO / "research" / "tools"), Path(footage).name)
    return CTX


def _ctx(ctx: Context | None) -> Context | None:
    return ctx or CTX


# ------------------------------------------------------------------ telemetry: candidates + roll
#
# Candidates and the roll series come from the camera's own sensors (R11's rules). TELEM's
# `roughcut.telemetry_events` will own this; until then the adapter below reads the GoPro
# stream directly. Swap it with `set_candidate_source(fn)` — `fn(ctx, clip) -> [candidate]`
# where a candidate is {"kind": air|impact|speed_drop|stop, "start", "end", "t", ...numbers}.

CandidateSource = Callable[[Context, str], list]
_SOURCE: CandidateSource | None = None


def set_candidate_source(fn: CandidateSource | None) -> None:
    global _SOURCE
    _SOURCE = fn


def _tel_module(tools: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("rc_telemetry_local", tools / "telemetry.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["rc_telemetry_local"] = m
    spec.loader.exec_module(m)
    return m


G = 9.80665


def read_sensors(ctx: Context, clip: str) -> dict | None:
    """ACCL |a| (g), the GRAV roll, GPS speed — from the telemetry stage's .gpmd.bin dump (or
    the file itself). None when the clip has no GoPro stream."""
    T = _tel_module(ctx.tools)
    video = ctx.video(clip)
    stem = Path(clip).stem
    idx = T.gpmd_stream_index(video)
    if idx is None:
        return None
    packets = T.packet_table(video, idx)
    dump = ctx.telemetry_dir() / f"{stem.upper()}.gpmd.bin"
    if not dump.exists():
        dump.parent.mkdir(parents=True, exist_ok=True)
    buf = dump.read_bytes() if dump.exists() else T.dump_gpmd(video, idx, dump)
    S = T.collect_streams(buf, packets)["streams"]
    if "ACCL" not in S or "GRAV" not in S:
        return None
    acc = S["ACCL"]
    ta = np.array(acc["t"])
    mag = np.linalg.norm(np.array(acc["v"]), axis=1) / G
    gr = S["GRAV"]
    tg = np.array(gr["t"])
    gv = np.array(gr["v"])                              # (lateral, vertical, forward)
    base = -1.0 if np.median(gv[:, 1]) < 0 else 1.0     # inverted mount: the camera turned the image
    roll = np.degrees(np.arctan2(gv[:, 0] * base, gv[:, 1] * base))
    gxy = np.hypot(gv[:, 0], gv[:, 1])
    tp, sp = np.zeros(0), np.zeros(0)
    gp = S.get("GPS5")
    if gp:
        ok = [(t, v[3]) for t, v, f, d in zip(gp["t"], gp["v"], gp["fix"], gp["dop"])
              if f is not None and f >= 2 and (d is None or d < 500)]
        if ok:
            tp, sp = np.array([o[0] for o in ok]), np.array([o[1] for o in ok])
    return dict(ta=ta, mag=mag, tg=tg, roll=roll, gxy=gxy, tp=tp, sp=sp, base=base)


def _runs_below(t, mag, floor=0.5, min_s=0.25):
    runs, s, lo = [], None, 9.0
    for ti, m in zip(t, mag):
        if m < floor:
            if s is None:
                s, lo = ti, m
            lo = min(lo, m)
        elif s is not None:
            if ti - s >= min_s:
                runs.append((s, ti, lo))
            s = None
    return runs


def candidates_from_sensors(d: dict) -> list[dict]:
    """R11's rules, as the bake-off ran them: |a| < 0.5 g for >= 0.25 s is an air; a > 5 g
    10 Hz peak an impact; a speed drop >= 3 m/s within 3 s; a stop 4 -> 1 m/s."""
    out: list[dict] = []
    ta, mag = d["ta"], d["mag"]
    if len(ta) < 2:
        return out
    for s, e, lo in _runs_below(ta, mag):
        m = (ta >= e) & (ta <= e + 0.6)
        land = float(mag[m].max()) if m.any() else 0.0
        out.append(dict(kind="air", start=round(float(s), 2), end=round(float(e), 2),
                        t=round(float((s + e) / 2), 2), min_g=round(float(lo), 3), landing_g=round(land, 2)))
    nb = int(ta[-1] * 10) + 1
    mx = np.zeros(nb)
    np.maximum.at(mx, np.minimum((ta * 10).astype(int), nb - 1), mag)
    for i in [i for i in range(nb) if mx[i] > 5 and mx[i] == mx[max(0, i - 5):i + 6].max()]:
        out.append(dict(kind="impact", start=round(i / 10, 2), end=round(i / 10 + .1, 2),
                        t=round(i / 10 + .05, 2), peak_g=round(float(mx[i]), 2)))
    tp, sp = d["tp"], d["sp"]
    if len(tp) > 10:
        grid = np.arange(0, tp[-1], 0.1)
        v = np.interp(grid, tp, sp)
        last_drop = last_stop = -99.0
        for i in range(len(grid) - 30):
            w = v[i + 1:i + 31]
            if v[i] - w.min() >= 3 and grid[i] - last_drop > 3:
                j = i + 1 + int(w.argmin())
                out.append(dict(kind="speed_drop", start=round(float(grid[i]), 2), end=round(float(grid[j]), 2),
                                t=round(float(grid[j]), 2), from_ms=round(float(v[i]), 1), to_ms=round(float(w.min()), 1)))
                last_drop = grid[i]
            if v[i] >= 4 and w.min() <= 1 and grid[i] - last_stop > 5:
                j = i + 1 + int(np.argmax(w <= 1))
                out.append(dict(kind="stop", start=round(float(grid[i]), 2), end=round(float(grid[j]), 2),
                                t=round(float(grid[j]), 2), from_ms=round(float(v[i]), 1)))
                last_stop = grid[i]
    out.sort(key=lambda c: c["t"])
    return out


def prepare(ctx: Context, clip: str) -> dict:
    """Candidates + the levelling sensors file for a clip, cached under <work>/local/<bin>/.
    -> {"candidates": [...], "sensors": path | None, "telemetry": bool}"""
    ctx.dir.mkdir(parents=True, exist_ok=True)
    stem = Path(clip).stem
    cj, npz = ctx.dir / f"{stem}.candidates.json", ctx.dir / f"{stem}.sensors.npz"
    if cj.exists():
        try:
            return {"candidates": json.loads(cj.read_text(encoding="utf-8")),
                    "sensors": str(npz) if npz.exists() else None, "telemetry": True}
        except (OSError, ValueError):
            pass
    d = read_sensors(ctx, clip)
    if d is None:
        return {"candidates": [], "sensors": None, "telemetry": False}
    cands = _SOURCE(ctx, clip) if _SOURCE else candidates_from_sensors(d)
    np.savez(str(npz) + ".tmp.npz", tg=d["tg"], roll=d["roll"], gxy=d["gxy"], base=np.array(d["base"]))
    os.replace(str(npz) + ".tmp.npz", npz)
    tmp = cj.with_name(cj.name + ".tmp")
    tmp.write_text(json.dumps(cands), encoding="utf-8")
    os.replace(tmp, cj)
    return {"candidates": cands, "sensors": str(npz), "telemetry": True}


# ------------------------------------------------------------------ running the CLI

class NotAvailable(RuntimeError):
    pass


def _run_json(args: list[str], timeout: float | None = None) -> dict:
    """One CLI call that prints one JSON document. Runs under the caller's process scope, so a
    job's Stop kills it."""
    r = subprocess.run(_cmd(*args), capture_output=True, text=True, timeout=timeout, env=_env())
    lines = (r.stdout or "").strip().splitlines()
    try:
        return json.loads(lines[-1])
    except (ValueError, IndexError):
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        raise RuntimeError(tail[-1][:200] if tail else f"rcgpu exit {r.returncode}")


def run_index(clips: list[str], ctx: Context | None = None, *, force: bool = False,
              on_event: Callable[[dict], None] | None = None, check: bool = True,
              embed: bool = True) -> dict:
    """Embed + check these clips in ONE subprocess (the models load once). Each clip's sidecar
    lands whole when it is done, so a stopped run keeps what finished. -> {"done": [stems, including clips whose sidecar was already there],
    "failed": {stem: error}, "peak_vram_gb", "events": [...]}"""
    ctx = _ctx(ctx)
    a = available()
    if not a["ok"] or ctx is None:
        raise NotAvailable(a["why"] if not a["ok"] else "no bin is open")
    ctx.dir.mkdir(parents=True, exist_ok=True)
    job = {"out": str(ctx.dir), "force": force, "check": check, "embed": embed, "clips": []}
    for clip in clips:
        pre = prepare(ctx, clip)
        job["clips"].append({"stem": Path(clip).stem, "video": str(ctx.video(clip)),
                             "candidates": pre["candidates"], "sensors": pre["sensors"]})
    jf = ctx.dir / f".job-{os.getpid()}-{threading.get_ident()}.json"
    jf.write_text(json.dumps(job), encoding="utf-8")
    events: list[dict] = []
    done: list[str] = []
    failed: dict[str, str] = {}
    peak = 0.0
    p = subprocess.Popen(_cmd("index", str(jf)), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, env=_env())
    err: list[str] = []
    t = threading.Thread(target=lambda: err.extend(p.stderr.read().splitlines()[-20:]), daemon=True)
    t.start()
    try:
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            events.append(ev)
            if ev.get("event") in ("clip_done", "clip_skipped"):      # skipped: its sidecar was already there
                done.append(ev["clip"])
                peak = max(peak, float(ev.get("peak_vram_gb") or 0))
            elif ev.get("event") == "clip_failed":
                failed[ev["clip"]] = str(ev.get("error"))
            if on_event:
                on_event(ev)
        rc = p.wait()
    finally:
        if p.poll() is None:
            p.kill()
        jf.unlink(missing_ok=True)
    t.join(timeout=2)
    if rc != 0 and not done and not failed:
        tail = [x for x in err if x.strip()]
        raise RuntimeError(tail[-1][:200] if tail else f"rcgpu index exited {rc}")
    return {"done": done, "failed": failed, "peak_vram_gb": peak, "events": events, "returncode": rc}


def sidecar_path(clip: str, ctx: Context | None = None) -> Path | None:
    c = _ctx(ctx)
    return None if c is None else c.dir / f"{Path(clip).stem}.local.json"


def sidecar(clip: str, ctx: Context | None = None) -> dict | None:
    p = sidecar_path(clip, ctx)
    if p is None or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def checks(clip: str, ctx: Context | None = None) -> list[dict]:
    return (sidecar(clip, ctx) or {}).get("checks") or []


def embed_clip(clip: str, ctx: Context | None = None, force: bool = False) -> dict:
    """Embeddings for one clip (Find). The stage does this together with the checks."""
    ctx = _ctx(ctx)
    a = available()
    if not a["ok"] or ctx is None:
        return {"ok": False, "why": a["why"] if not a["ok"] else "no bin is open"}
    try:
        r = run_index([clip], ctx, force=force, check=False)
    except (RuntimeError, OSError) as exc:
        return {"ok": False, "why": str(exc)[:200]}
    sc = sidecar(clip, ctx) or {}
    return {"ok": clip_stem(clip) in r["done"] or bool(sc), "why": "", **(sc.get("embed") or {})}


def clip_stem(clip: str) -> str:
    return Path(clip).stem


def check_window_ex(clip: str, start: float, end: float, questions=("fall",), ctx: Context | None = None,
                    scan: bool = False) -> dict:
    ctx = _ctx(ctx)
    qs = [questions] if isinstance(questions, str) else list(questions)
    a = available()
    if not a["ok"] or ctx is None:
        return {"ok": False, "why": a["why"] if not a["ok"] else "no bin is open", "p_yes": {q: None for q in qs}}
    try:
        pre = prepare(ctx, clip)
        args = ["check", "--video", str(ctx.video(clip)), "--start", f"{start:.3f}", "--end", f"{end:.3f}",
                "--questions", ",".join(qs)]
        if pre["sensors"]:
            args += ["--sensors", pre["sensors"]]
        if scan:
            args.append("--scan")
        r = _run_json(args)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "why": str(exc)[:200], "p_yes": {q: None for q in qs}}
    r.setdefault("why", "")
    return r


def check_window(clip: str, start: float, end: float, questions=("fall",),
                 ctx: Context | None = None) -> dict:
    """{question: P(yes)} over [start, end] of `clip`; None for a question not answered (no
    GPU / not installed / the run failed). A fall P(yes) >= 0.2 is worth a closer look; an
    air answer is advisory only. Frames are the 10 fps, levelled, sharpest-of-5 ones."""
    r = check_window_ex(clip, start, end, questions, ctx)
    return {q: (r.get("p_yes") or {}).get(q) for q in ([questions] if isinstance(questions, str) else questions)}


def search_ex(text, k: int = 3, clips: list[str] | None = None, ctx: Context | None = None) -> dict:
    ctx = _ctx(ctx)
    texts = [text] if isinstance(text, str) else list(text)
    a = available()
    if not a["ok"] or ctx is None:
        return {"ok": False, "why": a["why"] if not a["ok"] else "no bin is open", "results": {t: [] for t in texts}}
    args = ["search", "--dir", str(ctx.dir), "--k", str(k)]
    for t in texts:
        args += ["--text", t]
    for c in clips or []:
        args += ["--clips", Path(c).stem]
    try:
        r = _run_json(args)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "why": str(exc)[:200], "results": {t: [] for t in texts}}
    return {"ok": True, "why": "", **r}


def search(text: str, k: int = 3, clips: list[str] | None = None, ctx: Context | None = None) -> list[dict]:
    """Top-k windows {clip (stem), start, end, score} for a text query across the embedded clips."""
    return search_ex(text, k, clips, ctx)["results"].get(text, [])


# ------------------------------------------------------------------ Find from the vectors (numpy only)

def encode_texts(texts: list[str]) -> dict:
    """{"ok", "why", "model", "vectors": {text: [floats]}} — PE-Core text vectors via the
    subprocess (GPU lock held there). Small; the model load dominates."""
    a = available()
    if not a["ok"]:
        return {"ok": False, "why": a["why"], "vectors": {}}
    args = ["encode"]
    for t in texts:
        args += ["--text", t]
    try:
        r = _run_json(args, timeout=300)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "why": str(exc)[:200], "vectors": {}}
    return {"ok": True, "why": "", **r}


def load_items(directory: Path, clips: list[str] | None = None) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """[(clip stem, frame times, N x D embeddings)] from a bin's `<stem>.embed.json/.npy`."""
    want = {Path(c).stem for c in clips} if clips else None
    items = []
    for j in sorted(Path(directory).glob("*.embed.json")):
        try:
            meta = json.loads(j.read_text(encoding="utf-8"))
            npy = j.with_name(j.name.replace(".embed.json", ".embed.npy"))
            if not npy.exists() or (want and meta["clip"] not in want):
                continue
            items.append((meta["clip"], np.array(meta["times"], dtype=float), np.load(npy)))
        except (OSError, ValueError, KeyError):
            continue
    return items


def rank_windows(q, items, k: int = 3, win: float = 2.0, nms: float = 3.0, fps: float = 2.0) -> list[dict]:
    """The search the subprocess does (localgpu/rcgpu/embed.py search_windows), from a text vector:
    2 s smoothing, NMS 3 s, top-k `{clip, start, end, score, t}`; `t` is the scoring frame."""
    q = np.asarray(q, dtype=np.float32)
    cands = []
    for clip, t, e in items:
        if len(t) == 0:
            continue
        s = e.astype(np.float32) @ q
        w = max(1, int(round(win * fps)))
        sm = np.convolve(s, np.ones(w) / w, mode="same")
        cands += [(float(sm[i]), clip, float(t[i])) for i in range(len(t))]
    cands.sort(reverse=True)
    out: list[dict] = []
    for sc, clip, t in cands:
        if any(clip == o["clip"] and abs(t - o["t"]) < nms for o in out):
            continue
        out.append({"clip": clip, "start": round(max(0.0, t - win / 2), 2),
                    "end": round(t + win / 2, 2), "score": round(sc, 4), "t": round(t, 2)})
        if len(out) >= k:
            break
    return out
