"""The event scan and the ranking it feeds — `roughcut.events`.

No ffmpeg and no model calls: the motion track is the one thing here that shells out,
and it is exercised against a synthetic clip in test_server.py's fixture rather than
here. What must hold in this file is the arithmetic, because every judgement the
ranking makes rides on it:

  * a spike is found relative to *this clip's* own noise floor, not an absolute one
  * two peaks close together are one event and one window, not two of each
  * a window off the end of a clip is clamped, never negative and never past duration
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import events  # noqa: E402


def flat(n: int, value: float = 1.0) -> list[float]:
    return [value] * n


def test_robust_z_is_not_dragged_around_by_the_spike_it_is_looking_for():
    """Mean/sigma would hide the second spike behind the first. A quiet clip with two
    impacts must score both, which is the whole reason for median/MAD."""
    track = flat(200, 1.0)
    track[50] = 40.0
    track[150] = 20.0
    z = events.robust_z(track)
    assert z[50] > 20 and z[150] > 10
    assert abs(z[0]) < 1e-6


def test_a_busy_clip_and_a_quiet_one_are_scored_on_their_own_terms():
    """The same absolute jump means different things in a POV ski run and a lift
    cabin. Both must produce a peak; an absolute threshold would find one or the
    other, never both."""
    quiet = flat(200, 0.5)
    quiet[100] = 3.0
    busy = flat(200, 12.0)
    for i in range(200):
        busy[i] = 12.0 + (i % 5)
    busy[100] = 40.0
    assert max(events.robust_z(quiet)) > 3
    assert max(events.robust_z(busy)) > 3


def test_excitement_takes_either_signal_not_both():
    """A chase-cam jump barely moves the audio and a landing heard as a crunch can
    happen just off frame. Summing would demand both and find neither."""
    motion = flat(100, 1.0)
    motion[30] = 20.0
    onset = flat(100, 0.1)
    onset[70] = 5.0
    track = events.excitement(motion, onset, half=0)
    assert track[30] > 5 and track[70] > 5


def test_excitement_survives_a_clip_with_no_audio_track():
    motion = flat(100, 1.0)
    motion[30] = 20.0
    assert max(events.excitement(motion, [], half=0)) > 5


def test_two_peaks_inside_the_separation_are_one_event_not_two():
    """A take-off and its landing are 2s apart and are one thing to look at. The
    separation rule drops the weaker of the pair before any window is built."""
    track = flat(400, 0.0)
    track[200] = 9.0                                 # 20.0s
    track[220] = 8.0                                 # 22.0s — same event
    got = events.candidate_windows(track, hz=10.0, limit=5, duration=40.0)
    assert len(got) == 1 and got[0]["at"] == 20.0


def test_windows_are_clamped_to_the_clip_and_merged_when_they_overlap():
    """Far enough apart to be two peaks, close enough that their windows touch: one
    sheet covering both is cheaper than two, and reads better than two."""
    track = flat(400, 0.0)
    track[20] = 9.0        # 2.0s — window would start before zero
    track[85] = 8.0        # 8.5s — past the separation, but the windows overlap
    track[300] = 7.0       # 30.0s — its own window
    got = events.candidate_windows(track, hz=10.0, limit=5, duration=40.0)
    assert len(got) == 2, got
    assert got[0]["start"] == 0.0                    # clamped, not negative
    assert got[0]["end"] == 12.5                     # merged with the 8.5s peak
    assert got[0]["at"] == 2.0                       # the stronger peak names it
    assert got[1]["at"] == 30.0
    assert got[1]["end"] <= 40.0                     # clamped to the duration


def test_windows_stop_at_the_floor_rather_than_padding_to_the_limit():
    """Asking for eight windows in a clip where nothing happens must return none.
    A detector that always returns its quota spends model calls on flat footage."""
    track = flat(400, 0.0)
    track[100] = 9.0
    assert len(events.candidate_windows(track, hz=10.0, limit=8)) == 1


def test_peak_in_reports_when_not_just_how_much():
    track = flat(200, 0.0)
    track[137] = 6.0
    z, at = events.peak_in(track, 10.0, 16.0, hz=10.0)
    assert z == 6.0 and at == 13.7
    assert events.peak_in(track, 100.0, 110.0, hz=10.0) == (0.0, 100.0)


# ------------------------------------------------------- against real ffmpeg


def test_the_motion_track_is_real_and_sampled_at_the_rate_the_sidecars_use(project):
    """One clip through actual ffmpeg. The track has to line up sample-for-sample with
    the 10 Hz audio tracks, because `excitement` indexes them against each other —
    an off-by-one rate here would misplace every window in the bin."""
    track = events.motion_track(project["footage"] / "CLIP_A.MP4")
    assert 55 <= len(track) <= 62            # 6.0s at 10 Hz, ± ffmpeg's rounding
    assert max(track) > 0                    # testsrc2 moves; a flat track is a bug


def test_a_windowed_contact_sheet_labels_its_cells_in_clip_seconds(project, tmp_path):
    """The fine read's whole value is precise timestamps, and the window is extracted
    with an input seek, so ffmpeg's own clock restarts at zero. If the offset were
    lost, every fine moment would be reported near t=0 and look plausible."""
    import json
    import subprocess

    tool = Path(__file__).resolve().parents[2] / "research" / "tools" / "contact_sheet.py"
    out = tmp_path / "sheets"
    r = subprocess.run(
        ["uv", "run", "--quiet", str(tool), str(project["footage"] / "CLIP_A.MP4"),
         "-o", str(out), "--interval", "1", "--cols", "3", "--rows", "5",
         "--start", "2", "--end", "5"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    cells = index["clips"][0]["sheets"][0]["cells"]
    assert index["clips"][0]["window"] == [2.0, 5.0]
    assert [c["t"] for c in cells][:3] == [2.0, 3.0, 4.0]


# ------------------------------------------------------------------ the audit (R10 follow-up)

def _claim(start, end, kind="jump", notable=True, what="rider in the air over a roller"):
    return {"start": start, "end": end, "kind": kind, "notable": notable, "what": what,
            "frames": [start], "confidence": "high"}


def test_the_close_look_goes_to_the_claims_before_the_motion_peaks():
    """R10: the motion scan's top windows never covered CLIP_07 168-196 or CLIP_11
    136-160, so two claims refuted by eye kept ranking 6th and 20th — and on Killington
    not one close-look moment ever agreed with a hot claim. The claims come first now,
    inside the same per-clip budget, and the highest-ranked one leads."""
    track = flat(600, 0.0)
    track[500] = 6.0                                   # one big motion peak at 50 s
    coarse = {"moments": [_claim(20.0, 24.0, kind="action"),
                          _claim(10.0, 12.0, kind="jump", notable=False),
                          _claim(30.0, 32.0, kind="fall")]}
    w = events.choose_windows("CLIP_X.MP4", coarse, {}, track, limit=3)
    assert [x["source"] for x in w] == ["claim", "claim", "motion"]
    # notable first, then score: the fall leads, the non-notable jump follows
    assert w[0]["claims"][0]["kind"] == "fall" and w[0]["start"] <= 30 and w[0]["end"] >= 32
    assert w[1]["claims"][0]["kind"] == "jump"
    assert w[2]["at"] == 50.0
    # an action is not a hot claim and never buys a window of its own
    assert all(c["kind"] != "action" for x in w for c in x.get("claims", []))
    # the budget is the caller's, unchanged: claims take slots, they do not add any
    assert len(events.choose_windows("CLIP_X.MP4", coarse, {}, track, limit=1)) == 1


def test_a_claim_already_read_is_not_bought_twice_and_neither_is_its_window():
    track = flat(600, 0.0)
    track[310] = 6.0                                   # the peak sits in the read window
    track[500] = 5.0
    coarse = {"moments": [_claim(30.0, 32.0, kind="fall"), _claim(40.0, 42.0)]}
    fine = {"windows_read": [[27.0, 35.0]], "moments": [], "unusable": []}
    w = events.choose_windows("CLIP_X.MP4", coarse, fine, track, limit=3)
    spans = [(x["start"], x["end"]) for x in w]
    assert all(not (s <= 31.0 <= e) for s, e in spans), spans    # the fall: read
    assert w[0]["source"] == "claim" and w[0]["claims"][0]["start"] == 40.0
    assert [x["at"] for x in w if x["source"] == "motion"] == [50.0]   # 31 s skipped


def test_claims_close_together_share_one_window_and_a_long_claim_is_still_covered():
    # two claims two seconds apart are one window, priced once
    plan = events.plan_claims(
        [{"clip": "A", "start": 10.0, "end": 11.0, "kind": "jump", "score": 1.0},
         {"clip": "A", "start": 12.0, "end": 13.0, "kind": "fall", "score": 0.9}],
        limit=5)
    assert len(plan) == 1 and len(plan[0]["claims"]) == 2
    # a "jump 136-160" read through an 8 s window would come back still unseen
    lo, hi = events.claim_window(136.0, 160.0)
    assert hi - lo <= events.FINE_SHEET_S
    assert events._covered({"start": 136.0, "end": 160.0}, [(lo, hi)]) >= events.COVERED
    # and a window near the end of a clip stays inside it, full width where it can
    lo, hi = events.claim_window(58.0, 59.0, duration=60.0)
    assert hi == 60.0 and lo == 52.0


def test_a_fine_only_find_ranks_below_an_unaudited_claim_and_above_a_refuted_one():
    """R10: "a close look is a good auditor and a poor detector" — three of the four
    fine-only claims in its top fifteen were wrong by eye. One look at the busiest
    seconds no longer inherits UNSEEN's neutral 1.0."""
    jump = _claim(10.0, 12.0)
    fine_only = events.rank_clip("A", {}, {"moments": [jump], "windows_read": [[6, 14]]})
    unseen = events.rank_clip("A", {"moments": [jump]}, {})
    contradicted = events.rank_clip("A", {"moments": [jump]}, {
        "windows_read": [[6.0, 14.0]], "unusable": [],
        "moments": [{**jump, "kind": "junk", "what": "a glove"}]})
    assert fine_only[0]["why_ranked"]["confirmation"] == "fine-only"
    claim = [e for e in contradicted if e["kind"] == "jump"][0]
    assert claim["why_ranked"]["confirmation"] == "contradicted"
    assert claim["score"] < fine_only[0]["score"] < unseen[0]["score"]
    # two looks agreeing is still `confirmed`
    both = events.rank_clip("A", {"moments": [jump]},
                            {"moments": [jump], "windows_read": [[6, 14]]})
    assert [e["why_ranked"]["confirmation"] for e in both] == ["confirmed"]


def test_the_rebuilt_events_file_names_the_fine_only_weight(tmp_path):
    vis = tmp_path / "visual"
    vis.mkdir()
    payload = events.build(vis, tmp_path / "audio")
    assert payload["params"]["fine_only"] == events.FINE_ONLY
    assert payload["params"]["confirmation"]["fine-only"] == events.FINE_ONLY
    assert events.CONTRADICTED < events.UNSUPPORTED < events.FINE_ONLY < events.UNSEEN


def test_the_scan_tool_writes_the_claim_window_first(project, tmp_path):
    """The real event_scan.py over a real clip: the windows file the close look reads
    leads with the coarse claim, not with whatever moved most."""
    import json
    import subprocess

    visual = tmp_path / "visual"
    visual.mkdir()
    (visual / "CLIP_A.visual.json").write_text(json.dumps({
        "clip": "CLIP_A.MP4", "unusable": [],
        "moments": [{"start": 1.0, "end": 2.0, "kind": "fall", "notable": True,
                     "what": "rider down on the snow", "frames": [1.0]}]}),
        encoding="utf-8")
    tool = Path(__file__).resolve().parents[2] / "research" / "tools" / "event_scan.py"
    out = tmp_path / "windows.json"
    r = subprocess.run(
        ["uv", "run", "--quiet", str(tool), str(project["footage"]),
         "--sidecars", str(project["sidecars"]), "--visual", str(visual),
         "--only", "CLIP_A", "--limit", "1", "--windows-out", str(out)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "claim: fall" in r.stdout
    lo, hi = json.loads(out.read_text(encoding="utf-8"))["CLIP_A"][0]
    assert lo <= 1.0 and hi >= 2.0
