"""The visual pass's validation — what a contact sheet may claim (Karl's third report,
2026-09-08: "several clips say things that are not true").

Adjudicated against the rebuilt sheets: CLIP_04's 0:04 "person inverted or airborne"
is the wearer's glove; its 4:12 "backflip captured inverted" is a binding; CLIP_07's
"airborne across 8 consecutive frames" is two people standing on a slope shot from
below. The rules here are what stops each of those from reaching the ranker as a
notable event, and the frames the sheet names are what the pass shows.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def vp():
    spec = importlib.util.spec_from_file_location(
        "visual_pass", ROOT / "research" / "tools" / "visual_pass.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TIMES = [float(t) for t in range(0, 120, 4)]


def _one(vp, **m):
    row = {"start": 4.0, "end": 8.0, "what": "skier at 0:04 mid-air, skis level",
           "kind": "jump", "frames": [4.0], "confidence": "high", "notable": True}
    row.update(m)
    return vp.validate({"moments": [row], "unusable": [], "summary": ""},
                       0.0, 120.0, TIMES)["moments"][0]


def test_a_plain_claim_on_a_named_frame_stays_notable(vp):
    m = _one(vp)
    assert m["notable"] is True and "demoted" not in m
    assert m["frames"] == [4.0] and m["confidence"] == "high"


def test_hedged_wording_is_a_guess_not_a_claim(vp):
    m = _one(vp, what="Person in dark clothing appears to be inverted or airborne, suggesting a trick")
    assert m["notable"] is False and "hedged" in m["demoted"]


def test_nothing_is_airborne_for_eight_frames(vp):
    m = _one(vp, start=40.0, end=68.0, frames=[40, 44, 48, 52, 56, 60, 64, 68],
             what="person airborne in sustained jump sequence across 8 consecutive frames")
    assert m["notable"] is False and "not a jump" in m["demoted"]


def test_an_event_without_a_frame_or_without_high_confidence_is_not_notable(vp):
    # with no sampled times to snap to, an event that names no frame is demoted
    m = vp.validate({"moments": [{"start": 5.0, "end": 6.0, "what": "skier mid-air",
                                  "kind": "jump", "confidence": "high", "notable": True}],
                     "unusable": [], "summary": ""}, 0.0, 120.0, None)["moments"][0]
    assert m["notable"] is False and m["demoted"] == "no frame named for an event"
    m = _one(vp, confidence="medium")
    assert m["notable"] is False and m["demoted"] == "confidence medium"


def test_pov_gear_scenery_and_junk_are_never_notable(vp):
    for kind in ("pov-gear", "scenery", "junk"):
        m = _one(vp, kind=kind, what="glove across the lens")
        assert m["notable"] is False and "never notable" in m["demoted"]


def test_edges_snap_to_sampled_frames_and_sub_second_moments_become_one_frame(vp):
    m = _one(vp, start=1.0, end=1.1, frames=[], kind="action", notable=False,
             what="one or more figures active on slope")
    assert (m["start"], m["end"]) == (0.0, 0.0) and m["frames"] == [0.0]
    m = _one(vp, start=6.0, end=13.0, frames=[], kind="action", notable=False, what="riding")
    assert (m["start"], m["end"]) == (4.0, 12.0) and m["frames"] == [4.0, 8.0, 12.0]


def test_the_prompt_names_the_camera_the_frames_and_the_rules(vp):
    p = vp.PROMPT.format(n=30, clip="CLIP_04.MP4", interval=4, start=0, end=116,
                         stamps="0, 4, 8")
    for phrase in ("frames", "glove", "pov-gear", "airborne", "appears", "nothing notable"):
        assert phrase.lower() in p.lower(), phrase
    assert "helmet" in vp.SYSTEM and "never in frame" in vp.SYSTEM
    assert vp.PROMPT_VERSION >= 2
