"""The cut's clips get their 720p proxies first (verdict 5 punch 2; wave 6 lane PLAYBACK).

Verdict 5: "Program shows the 640×360 preview for the first ~90 min" of a Killington
project — the bin's proxies were built in the journal's priority order, whatever the cut
played. Now every builder puts the clips the cut plays first, in film order (V1's shots,
then V2+ / A3+, then a proposal nobody has answered), then the rest as before; a clip the
human just opened in a monitor still goes before all of them.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        pytest app/tests/test_proxy_order.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_index import _stub_tools, _wait_index
from test_server import _fresh

CUT = {
    "variant": "T", "title": "order", "orient": "none", "story": "", "target_s": [5, 20],
    # film order C, B; A is in the bin and not in the cut
    "segments": [
        {"id": "s1", "clip": "CLIP_C.MP4", "in": 0.0, "out": 2.0, "why": "first"},
        {"id": "s2", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"},
    ],
}


def _record_builds(server, monkeypatch) -> list[str]:
    built: list[str] = []

    def build(src: Path, dest: Path, orient: str) -> None:
        built.append(src.name)
        dest.write_bytes(b"")                 # a proxy on disk, for the stages after it

    monkeypatch.setattr(server, "build_proxy", build)
    monkeypatch.setattr(server, "measure_colour", lambda clip, force=False: None)
    return built


def _edl(tmp_path: Path, cut: dict) -> Path:
    p = tmp_path / "cut.json"
    p.write_text(json.dumps(cut, indent=1), encoding="utf-8")
    return p


def test_ensure_proxies_builds_the_cuts_clips_first_in_film_order(tmp_path, project, monkeypatch):
    import server

    with _fresh(tmp_path, project, edl=_edl(tmp_path, CUT)):
        built = _record_builds(server, monkeypatch)
        server.MEDIA["wanted"].clear()
        server.ensure_proxies(["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"])
    assert built == ["CLIP_C.MP4", "CLIP_B.MP4", "CLIP_A.MP4"]


def test_a_clip_the_human_opened_still_goes_before_the_cut(tmp_path, project, monkeypatch):
    import server

    with _fresh(tmp_path, project, edl=_edl(tmp_path, CUT)) as c:
        built = _record_builds(server, monkeypatch)
        server.MEDIA["wanted"].clear()
        c.get("/media/status/CLIP_A.mp4")
        server.ensure_proxies(["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"])
    server.MEDIA["wanted"].clear()
    assert built == ["CLIP_A.MP4", "CLIP_C.MP4", "CLIP_B.MP4"]


def test_a_waiting_proposals_clips_count_as_the_cut(tmp_path, project, monkeypatch):
    """The first pass's proposal is the cut-to-be before its Accept: its clips first."""
    import server

    empty = {**CUT, "segments": []}
    with _fresh(tmp_path, project, edl=_edl(tmp_path, empty)):
        asks: Path = server.STATE["asks"]
        asks.mkdir(parents=True, exist_ok=True)
        (asks / "job1.json").write_text(json.dumps({
            "job": "job1", "created": 9e9,
            "plan": {"segments": [{"clip": "CLIP_B.MP4", "in": 0, "out": 1}]}}), encoding="utf-8")
        assert server.cut_clips() == ["CLIP_B.MP4"]
        built = _record_builds(server, monkeypatch)
        server.MEDIA["wanted"].clear()
        server.ensure_proxies(["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"])
    assert built[0] == "CLIP_B.MP4"


def test_the_index_builds_the_cuts_proxies_before_the_rest(tmp_path, project, monkeypatch):
    """The index's journal walks the bin in capture order here (A, B, C by name, the
    fixture's clips sharing a capture time); the cut plays C then B, so the proxy pool
    takes C, B, then A."""
    import server

    with _fresh(tmp_path, project, edl=_edl(tmp_path, CUT),
                sidecars=project["sidecars"], visual=None) as c:
        _stub_tools(server, monkeypatch, tmp_path)
        built = _record_builds(server, monkeypatch)
        server.MEDIA["wanted"].clear()
        r = c.post("/api/index", json={"order": "capture"})
        assert r.status_code == 200, r.text
        s = _wait_index(c, r.json()["job"])
        assert s["state"] == "done", s
    assert built == ["CLIP_C.MP4", "CLIP_B.MP4", "CLIP_A.MP4"]


def test_without_a_cut_the_order_is_unchanged(tmp_path, project, monkeypatch):
    import server

    with _fresh(tmp_path, project, edl=_edl(tmp_path, {**CUT, "segments": []})):
        built = _record_builds(server, monkeypatch)
        server.MEDIA["wanted"].clear()
        server.ensure_proxies(["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"])
    assert built == ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]


@pytest.fixture(autouse=True)
def _clean_wanted():
    import server
    server.MEDIA["wanted"].clear()
    yield
    server.MEDIA["wanted"].clear()
