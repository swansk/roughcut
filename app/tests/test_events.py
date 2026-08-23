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
