"""The local GPU stage (wave 20, lane LOCALGPU) — everything but the GPU.

The CLI the stage drives (`python -m rcgpu`) is replaced by a fake that writes the same
sidecars, so what is tested is the plumbing a CPU-only CI can check: availability, the
journal stage (optional, background, GPU pool, depends on telemetry), the batch, resume, a
skip when nothing is installed, the lock, and the telemetry adapter. The real run is
scripts/score_local_checks.py on the Killington bin (research/R12-understanding).
"""
from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "localgpu"))

from roughcut import gpulock, journal, localvlm  # noqa: E402
from rcgpu import rules  # noqa: E402

from test_index import _stub_tools, _wait_index  # noqa: E402
from test_server import _fresh  # noqa: E402


# ------------------------------------------------------------------ a fake local stack

FAKE_CLI = '''#!{python}
import json, sys, time
from pathlib import Path
args = sys.argv[1:]
assert args[:2] == ["-m", "rcgpu"], args
cmd = args[2]
def emit(**kw): print(json.dumps(kw), flush=True)
if cmd == "index":
    job = json.loads(Path(args[3]).read_text())
    out = Path(job["out"]); out.mkdir(parents=True, exist_ok=True)
    log = out / "fake-cli.log"
    with log.open("a") as fh: fh.write(json.dumps([c["stem"] for c in job["clips"]]) + "\\n")
    for c in job["clips"]:
        side = out / (c["stem"] + ".local.json")
        if side.exists() and not job.get("force"):
            emit(event="clip_skipped", clip=c["stem"]); continue
        time.sleep({sleep})
        checks = [{{"t": k["t"], "cand": k, "q": "fall", "p_yes": 0.5, "verdict": "likely",
                   "threshold": 0.2, "settles": True}} for k in c["candidates"]]
        side.write_text(json.dumps({{"clip": c["stem"], "checks": checks, "embed": {{"n": 4}},
                                    "gpu": {{"gpu_s": 1.5, "peak_vram_gb": 5.7}}, "model": "fake"}}))
        emit(event="clip_done", clip=c["stem"], checks=len(checks), gpu_s=1.5, peak_vram_gb=5.7)
    emit(event="done", clips=len(job["clips"]))
elif cmd == "scan":
    job = json.loads(Path(args[3]).read_text())
    out = Path(job["out"]); out.mkdir(parents=True, exist_ok=True)
    (out / "fake-scan-job.json").write_text(json.dumps(job))
    for c in job["clips"]:
        scanned = [{{"start": w["start"], "end": w["end"], "at": w["at"], "motion_z": w["z"],
                    "p_yes": 0.8 if w["at"] > 10 else 0.1}} for w in c["windows"]]
        cands = [{{"kind": "air", "who": "other", "source": "local-scan", "q": "other_air",
                  "start": w["at"] - 0.75, "end": w["at"] + 0.75, "t": w["at"], "p_yes": w["p_yes"],
                  "verdict": "advisory", "settles": False}} for w in scanned if w["p_yes"] >= job.get("p_min", 0.5)]
        (out / (c["stem"] + ".otherair.json")).write_text(json.dumps({{"clip": c["stem"], "windows": len(scanned),
                                                                     "scanned": scanned, "candidates": cands}}))
        emit(event="clip_done", clip=c["stem"], candidates=len(cands), windows=len(scanned))
    emit(event="done", clips=len(job["clips"]))
elif cmd == "check":
    qs = args[args.index("--questions") + 1].split(",")
    print(json.dumps({{"ok": True, "p_yes": {{q: 0.31 for q in qs}}, "frames": {{"n": 31}}}}))
elif cmd == "search":
    print(json.dumps({{"results": {{args[args.index("--text") + 1]: [{{"clip": "CLIP_A", "start": 1.0, "end": 3.0, "score": 0.4}}]}}}}))
'''


@pytest.fixture()
def fake_stack(tmp_path, monkeypatch):
    """A venv whose python is the fake CLI, a fake nvidia-smi, the model folders."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    models = tmp_path / "models"
    (models / "qwen35-4b-bnb4").mkdir(parents=True)
    (models / "hf/hub/models--timm--PE-Core-L-14-336/snapshots/x").mkdir(parents=True)
    smi = tmp_path / "bin"
    smi.mkdir()
    (smi / "nvidia-smi").write_text("#!/bin/sh\necho 'GPU 0: Fake 3080 (UUID: GPU-0)'\n")
    (smi / "nvidia-smi").chmod(0o755)

    def write(sleep=0.0):
        cli = venv / "bin" / "python"
        cli.write_text(FAKE_CLI.format(python=sys.executable, sleep=sleep))
        cli.chmod(cli.stat().st_mode | stat.S_IEXEC)

    write()
    monkeypatch.setenv("ROUGHCUT_LOCALGPU", "1")
    monkeypatch.setenv("ROUGHCUT_LOCALGPU_VENV", str(venv))
    monkeypatch.setenv("ROUGHCUT_MODELS", str(models))
    monkeypatch.setenv("PATH", f"{smi}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ROUGHCUT_GPU_LOCK", str(tmp_path / "gpu.lock"))
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    localvlm._AVAIL.update(at=0.0, val=None)
    yield write
    localvlm._AVAIL.update(at=0.0, val=None)


# ------------------------------------------------------------------ availability

def test_off_by_the_environment_and_by_a_hidden_gpu(fake_stack, monkeypatch):
    assert localvlm.available(refresh=True)["ok"]
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    a = localvlm.available(refresh=True)
    assert not a["ok"] and "CUDA_VISIBLE_DEVICES" in a["why"]
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES")
    monkeypatch.setenv("ROUGHCUT_LOCALGPU", "0")
    assert not localvlm.available(refresh=True)["ok"]


def test_a_missing_install_or_model_says_so(fake_stack, monkeypatch, tmp_path):
    monkeypatch.setenv("ROUGHCUT_LOCALGPU_VENV", str(tmp_path / "nowhere"))
    a = localvlm.available(refresh=True)
    assert not a["ok"] and "install-local-models" in a["why"]
    monkeypatch.setenv("ROUGHCUT_LOCALGPU_VENV", str(tmp_path / "venv"))
    monkeypatch.setenv("ROUGHCUT_MODELS", str(tmp_path / "empty"))
    a = localvlm.available(refresh=True)
    assert not a["ok"] and "Qwen" in a["why"] and "PE-Core" in a["why"]


def test_every_call_degrades_to_not_available(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUGHCUT_LOCALGPU", "0")
    ctx = localvlm.Context(tmp_path, tmp_path / "w", ROOT / "research" / "tools", "bin")
    assert localvlm.check_window("A.MP4", 1, 2, ("fall", "air"), ctx) == {"fall": None, "air": None}
    assert localvlm.check_window_ex("A.MP4", 1, 2, "fall", ctx)["ok"] is False
    assert localvlm.search("a fall", 3, ctx=ctx) == []
    assert localvlm.embed_clip("A.MP4", ctx)["ok"] is False
    assert localvlm.checks("A.MP4", ctx) == [] and localvlm.sidecar("A.MP4", ctx) is None
    with pytest.raises(localvlm.NotAvailable):
        localvlm.run_index(["A.MP4"], ctx)


def test_the_api_runs_the_cli(fake_stack, tmp_path):
    ctx = localvlm.Context(tmp_path, tmp_path / "w", ROOT / "research" / "tools", "bin")
    ctx.dir.mkdir(parents=True)
    (ctx.dir / "A.sensors.npz").write_bytes(b"")           # prepare() finds its cache
    (ctx.dir / "A.candidates.json").write_text("[]")
    r = localvlm.check_window("A.MP4", 1, 3, ("fall", "air"), ctx)
    assert r == {"fall": 0.31, "air": 0.31}
    assert localvlm.search("x", 3, ctx=ctx)[0]["clip"] == "CLIP_A"


# ------------------------------------------------------------------ other riders' airs

def _motion_bin(tmp_path, spikes):
    """A bin whose clip A has a flat motion track with spikes at the given seconds (10 Hz)."""
    ctx = localvlm.Context(tmp_path, tmp_path / "w", ROOT / "research" / "tools", "bin")
    vis = ctx.work / "visual" / "bin"
    vis.mkdir(parents=True)
    m = [1.0 + 0.01 * (i % 7) for i in range(600)]
    for t in spikes:
        for k in range(-4, 5):
            m[int(t * 10) + k] += 40.0 - 8 * abs(k)
    (vis / "A.motion.json").write_text(json.dumps({"clip": "A.mp4", "hz": 10.0, "width": 160, "mafd": m}))
    ctx.dir.mkdir(parents=True)
    (ctx.dir / "A.candidates.json").write_text("[]")
    return ctx


def test_windows_are_motion_peaks_without_a_telemetry_air(tmp_path):
    ctx = _motion_bin(tmp_path, [12.0, 30.0, 50.0])
    w = localvlm.other_air_windows(ctx, "A.MP4")
    assert sorted(round(x["at"]) for x in w) == [12, 30, 50]
    assert all(x["end"] - x["start"] >= 3.9 for x in w)
    (ctx.dir / "A.candidates.json").write_text(json.dumps(
        [{"kind": "air", "start": 29.6, "end": 30.0, "t": 29.8}, {"kind": "impact", "start": 49.9, "end": 50.0, "t": 50.0}]))
    w = localvlm.other_air_windows(ctx, "A.MP4", telemetry_airs=[{"kind": "air", "start": 29.6, "end": 30.0}])
    assert sorted(round(x["at"]) for x in w) == [12, 50], "the wearer's own air is not another rider's"
    assert len(localvlm.other_air_windows(ctx, "A.MP4", limit=1)) == 1
    assert localvlm.other_air_windows(localvlm.Context(tmp_path, tmp_path / "none", ROOT, "bin"), "A.MP4") == []


def test_the_scan_runs_the_cli_and_reads_candidates_back(fake_stack, tmp_path):
    ctx = _motion_bin(tmp_path, [12.0, 5.0])
    (ctx.dir / "A.sensors.npz").write_bytes(b"")
    r = localvlm.scan_other_air(["A.MP4", "B.MP4"], ctx)          # B has no motion cache: nothing to ask
    assert r["done"] == ["A"] and r["windows"] == 2 and not r["failed"]
    job = json.loads((ctx.dir / "fake-scan-job.json").read_text())
    assert [c["stem"] for c in job["clips"]] == ["A"] and job["clips"][0]["windows"][0]["start"] >= 0
    c = localvlm.other_airs("A.MP4", ctx)
    assert len(c) == 1 and c[0]["who"] == "other" and c[0]["kind"] == "air" and c[0]["settles"] is False
    assert c[0]["verdict"] == "advisory" and c[0]["source"] == "local-scan"
    assert localvlm.other_airs("A.MP4", ctx, p_min=0.95) == []
    assert len(localvlm.other_air_scan("A.MP4", ctx)["scanned"]) == 2
    assert localvlm.other_airs("B.MP4", ctx) == [] and localvlm.other_air_scan("B.MP4", ctx) is None


def test_the_scan_degrades_without_the_stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUGHCUT_LOCALGPU", "0")
    ctx = localvlm.Context(tmp_path, tmp_path / "w", ROOT / "research" / "tools", "bin")
    with pytest.raises(localvlm.NotAvailable):
        localvlm.scan_other_air(["A.MP4"], ctx)
    assert localvlm.other_airs("A.MP4", ctx) == []


def test_an_other_air_is_a_candidate_moment_never_an_event():
    from roughcut import events
    m = events.other_air_moments("A", [
        {"kind": "air", "who": "other", "start": 24.5, "end": 26.0, "t": 25.2, "p_yes": 0.83},
        {"kind": "air", "who": "wearer", "start": 1, "end": 2, "t": 1.5}, {"kind": "fall", "who": "other"}])
    assert len(m) == 1 and m[0]["who"] == "other" and m[0]["status"] == "candidate"
    assert m[0]["source"] == "local-scan" and m[0]["kind"] == "jump"


def test_local_model_ids_live_in_config(monkeypatch):
    from roughcut import config
    from rcgpu import config as rc
    assert config.local_model(config.LOCAL_VLM)["repo"] == rc.QWEN_REPO == "Qwen/Qwen3.5-4B"
    assert config.local_model(config.LOCAL_EMBED)["repo"] == rc.PE_REPO
    monkeypatch.setenv("ROUGHCUT_LOCAL_VLM_DIR", "other-dir")
    assert config.local_model("vlm")["dir"] == "other-dir"
    with pytest.raises(ValueError):
        config.local_model("nope")


# ------------------------------------------------------------------ the rules

def test_an_air_is_never_settled_locally_and_a_fall_cut_is_point_two():
    assert rules.verdict("fall", 0.19) == "unlikely"
    assert rules.verdict("fall", 0.2) == "likely"
    assert rules.verdict("air", 0.99) == "advisory", "an air is never settled locally"
    assert rules.verdict("other_air", 0.99) == "advisory", "another rider's air is never settled locally either"
    assert rules.verdict("fall", None) == "unchecked"
    assert rules.questions_for({"kind": "air"}) == ["air"]
    assert rules.questions_for({"kind": "impact"}) == ["fall"]
    assert rules.questions_for({"kind": "label", "questions": ["fall"]}) == ["fall"]
    assert localvlm.FALL_THRESHOLD == rules.FALL_THRESHOLD


# ------------------------------------------------------------------ the telemetry adapter

def test_candidates_follow_the_r11_rules():
    ta = np.arange(0, 20, 0.005)
    mag = np.ones_like(ta)
    mag[(ta >= 5) & (ta < 5.4)] = 0.05            # a 0.4 s freefall
    mag[(ta >= 9) & (ta < 9.1)] = 0.05            # 0.1 s: too short
    mag[np.argmin(abs(ta - 5.6))] = 7.0           # the landing, an impact
    tp = np.arange(0, 20, 0.055)
    sp = np.where(tp < 12, 9.0, 2.0)              # a 7 m/s drop at 12 s
    out = localvlm.candidates_from_sensors(dict(ta=ta, mag=mag, tp=tp, sp=sp))
    kinds = [(c["kind"], c["t"]) for c in out]
    assert ("air", 5.2) in kinds and not any(k == "air" and 9 < t < 10 for k, t in kinds)
    assert any(k == "impact" and abs(t - 5.6) < 0.2 for k, t in kinds)
    assert any(k == "speed_drop" and 11 < t < 15 for k, t in kinds)
    assert all({"start", "end", "t"} <= set(c) for c in out)
    assert [c["t"] for c in out] == sorted(c["t"] for c in out)


def test_the_candidate_source_can_be_swapped(tmp_path, monkeypatch):
    ctx = localvlm.Context(tmp_path, tmp_path / "w", ROOT / "research" / "tools", "bin")
    d = dict(ta=np.zeros(2), mag=np.zeros(2), tg=np.arange(3.0), roll=np.zeros(3), gxy=np.ones(3),
             tp=np.zeros(0), sp=np.zeros(0), base=1.0)
    monkeypatch.setattr(localvlm, "read_sensors", lambda c, clip: d)
    localvlm.set_candidate_source(lambda c, clip: [{"kind": "air", "start": 1.0, "end": 1.4, "t": 1.2}])
    try:
        pre = localvlm.prepare(ctx, "A.MP4")
    finally:
        localvlm.set_candidate_source(None)
    assert pre["candidates"][0]["t"] == 1.2 and Path(pre["sensors"]).exists()
    z = np.load(pre["sensors"])
    assert float(z["base"]) == 1.0


# ------------------------------------------------------------------ the lock

def test_the_gpu_lock_serialises_and_a_waiter_is_seen(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUGHCUT_GPU_LOCK", str(tmp_path / "gpu.lock"))
    got = []
    first = gpulock.Lock("local")
    assert first.acquire()
    assert gpulock.holder()["owner"] == "local"

    def other():
        with gpulock.hold("asr"):
            got.append(time.time())

    th = threading.Thread(target=other)
    th.start()
    t0 = time.time()
    while "asr" not in gpulock.waiting() and time.time() - t0 < 5:
        time.sleep(0.05)
    assert "asr" in gpulock.waiting() and not got
    assert first.yield_to_waiters(pause=0.0), "the long job steps aside for the short one"
    th.join(5)
    assert got and gpulock.holder()["owner"] == "local"
    first.release()
    assert gpulock.holder() is None
    assert not gpulock.Lock("x").acquire(timeout=0) is False


def test_a_queued_wait_can_be_aborted(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUGHCUT_GPU_LOCK", str(tmp_path / "gpu.lock"))
    held = gpulock.Lock("local")
    held.acquire()
    with pytest.raises(TimeoutError):
        with gpulock.hold("asr", abort=lambda: True):
            pass
    assert gpulock.waiting() == []
    held.release()


# ------------------------------------------------------------------ the journal's stage

def test_local_is_optional_background_and_on_the_gpu_pool(tmp_path):
    j = journal.Journal(tmp_path / "j.json")
    j.add_clips(["A.MP4"], now=0.0)
    assert journal.DEPENDS["local"] == ("probe", "telemetry") and journal.KIND["local"] == "gpu"
    assert journal.DEFAULT_WORKERS["gpu"] == 1 and "local" in journal.BACKGROUND
    for s in ("probe", "telemetry", "asr", "proxy", "look", "close", "picks"):
        j.start("A.MP4", s, now=1.0)
        j.finish("A.MP4", s, now=2.0)
    assert j.state("A.MP4", "local") == "queued"
    assert j.released("A.MP4"), "the cut does not wait on the GPU"
    assert j.next(now=3.0) == ("A.MP4", "local")
    j.start("A.MP4", "local", now=3.0)
    j.add_clips(["B.MP4"], now=4.0)
    for s in ("probe", "telemetry"):
        j.start("B.MP4", s, now=4.0)
        j.finish("B.MP4", s, now=5.0)
    assert j.next(now=6.0) != ("B.MP4", "local"), "one local job at a time"
    # an old journal without the stage loads with it queued
    data = json.loads((tmp_path / "j.json").read_text())
    del data["clips"]["A.MP4"]["stages"]["local"]
    (tmp_path / "old.json").write_text(json.dumps(data))
    assert journal.Journal.load(tmp_path / "old.json").state("A.MP4", "local") == "queued"


def test_a_failed_local_run_never_parks_a_clip(tmp_path):
    """The server skips (never fails) the stage: a parked clip would stop its whole index."""
    j = journal.Journal(tmp_path / "j.json")
    j.add_clips(["A.MP4"], now=0.0)
    j.skip("A.MP4", "local", "failed: CUDA out of memory", now=1.0)
    assert j.clips["A.MP4"]["parked"] is None and j.state("A.MP4", "local") == "skipped"


# ------------------------------------------------------------------ the stage in the server

def _index_all(c, server, monkeypatch, tmp_path):
    _stub_tools(server, monkeypatch, tmp_path)
    return _wait_index(c, c.post("/api/index", json={}).json()["job"], timeout=90.0)


def test_the_stage_runs_every_clip_in_one_batch_and_resumes(fake_stack, tmp_path, project, monkeypatch):
    import server
    monkeypatch.setenv("ROUGHCUT_LOCALGPU", "1")
    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        s = _index_all(c, server, monkeypatch, tmp_path)
        assert s["state"] == "done", s
        j = server.load_journal()
        for clip in server.footage_clips():
            assert j.state(clip, "local") == "done", (clip, j.clips[clip]["stages"]["local"])
            assert server.local_sidecar(clip).exists()
        log = (server.local_dir() / "fake-cli.log").read_text().splitlines()
        assert len(log) == 1 and len(json.loads(log[0])) == 3, "one process, all three clips"
        rows = c.get("/api/index/clips").json()["rows"]
        loc = rows[0]["stages"]["local"]
        assert loc["journal"] == "done" and loc["label"] == "Local (3080)" and loc["file"]
        assert loc["gpu_s"] == 1.5 and loc["peak_vram_gb"] == 5.7
        # resume: sidecars are the truth — a second index runs nothing on the GPU again
        for clip in server.footage_clips():
            server.local_sidecar(clip).exists()
        j2 = server.load_journal()
        j2.reopen(server.footage_clips()[0], "local")
        j2.save()
        s = _index_all(c, server, monkeypatch, tmp_path)
        assert s["state"] == "done"
        assert server.load_journal().state(server.footage_clips()[0], "local") == "done"


def test_the_stage_is_skipped_when_nothing_is_available_and_taken_again_when_it_is(
        fake_stack, tmp_path, project, monkeypatch):
    import server
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    localvlm._AVAIL.update(at=0.0, val=None)
    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        s = _index_all(c, server, monkeypatch, tmp_path)
        assert s["state"] == "done", s
        j = server.load_journal()
        for clip in server.footage_clips():
            st = j.clips[clip]["stages"]["local"]
            assert st["state"] == "skipped" and st["last_error"].startswith("not available"), st
            assert j.released(clip) and j.clips[clip]["parked"] is None
        assert not server.local_dir().exists() or not list(server.local_dir().glob("*.local.json"))
        # the card shows up (or the stack is installed): the next index takes the clips again
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES")
        localvlm._AVAIL.update(at=0.0, val=None)
        s = _index_all(c, server, monkeypatch, tmp_path)
        assert s["state"] == "done", s
        j = server.load_journal()
        assert all(j.state(clip, "local") == "done" for clip in server.footage_clips())


def test_the_audio_pass_takes_the_gpu_lock_only_when_the_stack_is_installed(fake_stack, tmp_path, monkeypatch):
    import server
    with server.local_gpu_lock_for_audio():
        assert gpulock.holder()["owner"] == "asr"
    assert gpulock.holder() is None
    monkeypatch.setenv("ROUGHCUT_LOCALGPU", "0")
    with server.local_gpu_lock_for_audio():
        assert gpulock.holder() is None
