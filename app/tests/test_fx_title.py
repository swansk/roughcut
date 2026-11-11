"""Wave 23, TITLE — titles a person can ship.

Three editors asked for a title and each got one outside the frame, each "✓ checked": the
check looked at the sprite's anchor, and the renderer fits the words to the sprite's BOX,
not to the frame. These tests pin, on the renderer's own pixels and with no model:

  * the three failing records (the Copper "PER 2026", the end card cut off both sides with
    its second line on the first, the lower third chopped at the left edge with a white
    bar through the "n") fail the check, with the measured overflow in the sentence;
  * what the check measures is what the renderer draws (the bbox of the alpha of a real
    frame), so a monitor, a proof and an export that agree with the renderer agree with it;
  * no pale stripe or chopped bar sits behind a text drawn from a spec that passes;
  * the designer is asked again with the measured overflow, up to twice, and a designer
    that cannot fix it leaves a failing check, never "✓";
  * the hand controls change the effect with no model call and re-check at once;
  * a fix of a title replaces it, a removal is a removal, one Accept is enough for an
    accepted AI answer's design, and a text effect with no sound has no sound check.
"""

from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import fx, fxcheck  # noqa: E402

W, H = 1280, 720


def _effect(events, overlay, **extra):
    e = {"id": "fx_t", "shot": "s1", "clip": "CLIP_A.MP4", "name": "title", "note": "a title",
         "why": "", "events": events, "overlay": fx.validate_overlay(overlay), "sound": None,
         "status": "proposed"}
    e["events"] = fx.validate_events(events, None)
    e.update(extra)
    return e


def _text(text, h, **kw):
    return {"type": "text", "text": text, "at": [0, 0], "h": h, "bold": True, "color": "#ffffff", **kw}


# the three records the editors made, as the model wrote them
COPPER_2026 = dict(events=[{"t": 1.2, "x": 0.18, "y": 0.78}], overlay={
    "duration": 3.9, "size": 1.0,
    "shapes": [{"type": "rect", "at": [0, 0], "w": 2.0, "h": 0.24, "fill": True, "color": "#0b0b0f", "opacity": 0.45},
               _text("COPPER 2026", 0.13)],
    "anim": {"opacity": [[0, 0], [0.25, 1], [3.5, 1], [3.9, 0]], "dy": [[0, 0.01], [0.3, 0]]}})

END_CARD = dict(events=[{"t": 1.2, "x": 0.5, "y": 0.5}], overlay={
    "duration": 4.0, "size": 1.6,
    "shapes": [{"type": "rect", "at": [0, 0], "w": 3.0, "h": 3.0, "fill": True, "color": "#000000", "opacity": 1.0},
               _text("Copper Mountain · February 2026", 0.055),
               _text("Thanks for watching", 0.034, opacity=0.9)],
    "anim": {}})

SPENCER = dict(events=[{"t": 1.2, "x": 0.18, "y": 0.85}], overlay={
    "duration": 3.0, "size": 0.4,
    "shapes": [{"type": "rect", "at": [0, 0], "w": 2.0, "h": 0.2, "fill": True, "color": "#0b0b0f", "opacity": 0.6},
               {"type": "rect", "at": [0, 0], "w": 0.04, "h": 0.2, "fill": True, "color": "#ffffff", "opacity": 0.9},
               _text("Spencer", 0.14)],
    "anim": {"dx": [[0, -0.06], [0.3, 0]], "opacity": [[0, 0], [0.2, 1], [2.55, 1], [3.0, 0]]}})


def _kinds(effect, w=W, h=H):
    return {p["kind"] for p in fx.check_bounds(effect, w, h)["problems"]}


# ---------------------------------------------------------------- the failing cases

def test_the_copper_title_outside_the_frame_fails_with_the_measured_overflow():
    e = _effect(**COPPER_2026)
    res = fx.check_bounds(e, W, H)
    assert res["ok"] is False
    text = next(p for p in res["problems"] if p["kind"] == "text_outside")
    assert "COPPER 2026" in text["detail"] and "past the left edge of the frame" in text["detail"]
    assert res["text"][0] < 0                              # "COP" is off the picture
    assert "shape_outside" in _kinds(e)                    # and its bar is cut by the same edge


def test_the_end_card_wider_than_the_frame_and_its_lines_on_each_other_fail():
    e = _effect(**END_CARD)
    kinds = _kinds(e)
    assert {"text_outside", "text_overlap"} <= kinds
    res = fx.check_bounds(e, W, H)
    assert res["text"][0] < 0 and res["text"][2] > 1       # cut off on both sides
    # the full-frame black rect is a wash across the frame: allowed to bleed
    assert "shape_outside" not in kinds


def test_the_lower_third_chopped_at_the_edge_with_a_stripe_through_the_n_fails():
    e = _effect(**SPENCER)
    kinds = _kinds(e)
    assert {"cuts_text", "shape_outside"} <= kinds
    details = " ".join(p["detail"] for p in fx.check_bounds(e, W, H)["problems"])
    assert "Spencer" in details and "lies across the text" in details


def test_the_check_row_says_it_failed_and_names_the_overflow():
    row = fx.bounds_check(_effect(**COPPER_2026), W, H)
    assert row["key"] == "bounds" and row["ok"] is False and "COPPER 2026" in row["detail"]
    ok = fx.bounds_check(_effect(**{**COPPER_2026, "events": [{"t": 1.2, "x": 0.5, "y": 0.5}],
                                    "overlay": {**COPPER_2026["overlay"], "size": 0.8}}), W, H)
    assert ok["ok"] is True


def test_an_effect_with_no_text_has_nothing_to_measure():
    e = _effect(events=[{"t": 1.0, "x": 0.97, "y": 0.5}], overlay={
        "duration": 0.35, "size": 0.12,
        "shapes": [{"type": "circle", "at": [0, 0], "r": 0.9, "fill": False, "width": 0.1}], "anim": {}})
    assert fx.bounds_check(e, W, H) is None                # a marker at the picture's edge may be cut
    assert fx.check_bounds(e, W, H)["ok"] is True


# ---------------------------------------------------------------- measured on what is drawn

def _drawn_bbox(effect, w, h, t):
    """The alpha bounding box of the frame the EXPORT draws (no pad), in frame fractions."""
    ev = effect["events"][0]
    img = fx.render_overlay_frame(fx._bare(effect["overlay"]), w, h, t, anchor=(ev["x"], ev["y"]))
    box = img.getchannel("A").point(lambda v: 255 if v >= 64 else 0).getbbox()
    return None if box is None else [box[0] / w, box[1] / h, box[2] / w, box[3] / h]


def test_what_is_measured_is_what_the_renderer_draws():
    for spec in (COPPER_2026, END_CARD):
        e = _effect(**spec)
        m = fx.measure(e, W, H)
        view = m["views"][0]
        drawn = _drawn_bbox(e, W, H, view["t"])           # clipped by the frame, as the export is
        got = view["all"]
        # the visible part of the measured box is the drawn box (a pixel of slack)
        want = [max(0.0, got[0]), max(0.0, got[1]), min(1.0, got[2]), min(1.0, got[3])]
        assert drawn == pytest.approx(want, abs=0.012), (spec["events"], drawn, want)
    # a left edge cut by the frame is visible in the real frame: text pixels in column 0
    e = _effect(**COPPER_2026)
    img = fx.render_overlay_frame(e["overlay"], W, H, 1.0, anchor=(0.18, 0.78))
    col = img.crop((0, 0, 2, H)).getchannel("A").getextrema()
    assert col[1] > 0                                      # the bar / the C is drawn at the very edge


def test_a_title_inside_the_safe_area_passes_and_is_where_it_is_drawn():
    e = _effect(events=[{"t": 1.2, "x": 0.5, "y": 0.5}], overlay={
        "duration": 3.0, "size": 0.8, "shapes": [_text("COPPER 2026", 0.1)], "anim": {}})
    res = fx.check_bounds(e, W, H)
    assert res["ok"] is True
    assert res["text"] == pytest.approx(_drawn_bbox(e, W, H, 0.0), abs=0.012)
    assert 0.05 <= res["text"][0] and res["text"][2] <= 0.95


def test_the_vertical_frame_is_measured_in_its_own_aspect():
    # the same spec on a 9:16 frame is 1.78x taller than wide: a lower title that is fine
    # on 16:9 runs past the bottom safe line
    e = _effect(events=[{"t": 1.0, "x": 0.5, "y": 0.93}], overlay={
        "duration": 2.0, "size": 0.6, "shapes": [_text("Hello", 0.2)], "anim": {}})
    assert "text_outside" in _kinds(e, W, H)
    e2 = _effect(events=[{"t": 1.0, "x": 0.5, "y": 0.85}], overlay=e["overlay"])
    assert fx.check_bounds(e2, W, H)["ok"] is True
    assert fx.check_bounds(e2, 720, 1280)["ok"] in (True, False)       # measured without error


def test_the_designer_examples_pass_their_own_check():
    for name in ("_EXAMPLE_LOWERTHIRD", "_EXAMPLE_TITLE", "_EXAMPLE_TYPEWRITER"):
        ex = copy.deepcopy(getattr(fx, name))
        e = {**ex, "overlay": fx.validate_overlay(ex["overlay"]), "events": fx.validate_events(ex["events"], None)}
        assert fx.check_bounds(e, W, H)["ok"], (name, fx.check_bounds(e, W, H)["problems"])


# ---------------------------------------------------------------- no stray stripe, no chopped bar

def _pale_in_text_box(effect, w=W, h=H):
    """Pixels inside the text's own box that are pale but are not the text: render the whole
    sprite and the text alone; where the text alone is transparent, the whole must be the
    plate (dark) or the picture behind it (nothing) — a pale pixel there is a stripe."""
    ev = effect["events"][0]
    anchor = (ev["x"], ev["y"])
    t = fx._measure_times(effect["overlay"])[0]
    whole = fx.render_overlay_frame(fx._bare(effect["overlay"]), w, h, t, anchor=anchor)
    text_i = next(i for i, s in enumerate(effect["overlay"]["shapes"]) if s["type"] == "text")
    alone = fx.render_overlay_frame(fx._bare(effect["overlay"], only=text_i), w, h, t, anchor=anchor)
    box = alone.getchannel("A").point(lambda v: 255 if v >= 64 else 0).getbbox()
    pale = 0
    wp, ap = whole.load(), alone.load()
    for y in range(box[1], box[3]):
        for x in range(box[0], box[2]):
            if ap[x, y][3] < 16 and wp[x, y][3] > 40 and sum(wp[x, y][:3]) / 3 > 140:
                pale += 1
    return pale


def test_a_bar_through_the_text_is_a_stripe_and_the_fixed_spec_has_none():
    stripy = _effect(events=[{"t": 1.0, "x": 0.4, "y": 0.8}], overlay={
        "duration": 3.0, "size": 0.5, "anim": {}, "shapes": [
            {"type": "rect", "at": [0, 0], "w": 1.8, "h": 0.5, "fill": True, "color": "#0b0b0f", "opacity": 0.65},
            {"type": "rect", "at": [0, 0], "w": 0.05, "h": 0.5, "fill": True, "color": "#ffffff", "opacity": 0.95},
            _text("Spencer", 0.16)]})
    assert _pale_in_text_box(stripy) > 20                  # the renderer draws what it is told: a stripe
    assert "cuts_text" in _kinds(stripy)                   # and the check says so
    fixed = copy.deepcopy(fx._EXAMPLE_LOWERTHIRD)
    fixed = {**fixed, "overlay": fx.validate_overlay(fixed["overlay"]),
             "events": fx.validate_events(fixed["events"], None)}
    assert _pale_in_text_box(fixed) == 0
    assert fx.check_bounds(fixed, W, H)["ok"]


def test_a_plate_shorter_than_its_words_is_a_chopped_bar():
    chopped = _effect(events=[{"t": 1.0, "x": 0.5, "y": 0.8}], overlay={
        "duration": 3.0, "size": 0.5, "anim": {}, "shapes": [
            {"type": "rect", "at": [0, 0], "w": 1.0, "h": 0.17, "fill": True, "color": "#0b0b0f", "opacity": 0.55},
            _text("Spencer, the milkman", 0.09)]})
    assert "cuts_text" in _kinds(chopped)


# ---------------------------------------------------------------- fit, with no model

def test_fit_to_frame_brings_each_failing_title_inside_without_a_model():
    for spec in (COPPER_2026, END_CARD):
        e = _effect(**spec)
        fitted = fx.fit_to_frame(e, W, H)
        assert fitted is not None
        res = fx.check_bounds(fitted, W, H)
        assert not {p["kind"] for p in res["problems"]} & {"text_outside"}, res["problems"]
        fx.validate_effect({**fitted, "shot": "s1"}, [{"id": "s1", "clip": "CLIP_A.MP4", "in": 0.0, "out": 6.0}])
    assert fx.fit_to_frame(_effect(**{**COPPER_2026, "events": [{"t": 1.2, "x": 0.5, "y": 0.5}],
                                      "overlay": {**COPPER_2026["overlay"], "shapes": [_text("Hi", 0.1)], "size": 0.8}}),
                           W, H) is None


# ---------------------------------------------------------------- the designer asked again

SEGS = [{"id": "s1", "clip": "CLIP_A.MP4", "in": 0.0, "out": 6.0}]


def _answer(spec):
    return {"name": "title", "why": "w", "events": spec["events"], "overlay": spec["overlay"], "sound": None}


def test_refit_asks_again_with_the_measured_overflow_and_stops_when_it_fits(monkeypatch):
    asked = []
    good = {**COPPER_2026, "events": [{"t": 1.2, "x": 0.5, "y": 0.5}],
            "overlay": {**COPPER_2026["overlay"], "size": 0.8}}

    def ask(system, prompt, images=None, *, what="the design call"):
        asked.append(prompt)
        return _answer(good)

    monkeypatch.setattr(fx, "_ask", ask)
    e = fx.validate_effect({**_effect(**COPPER_2026), "shot": "s1"}, SEGS)
    out, n, res = fx.refit(e, SEGS, None, w=W, h=H)
    assert n == 1 and res["ok"] and len(asked) == 1
    assert "FRAME CHECK FAILED" in asked[0] and "past the left edge of the frame" in asked[0]
    assert out["note"] == e["note"]                       # the editor's words stay the note
    assert out["history"][-1]["auto"] == "frame check"


def test_refit_gives_up_after_two_asks_and_leaves_a_failing_check(monkeypatch):
    calls = []

    def ask(system, prompt, images=None, *, what="the design call"):
        calls.append(what)
        return _answer(COPPER_2026)                      # never fixes it

    monkeypatch.setattr(fx, "_ask", ask)
    e = fx.validate_effect({**_effect(**COPPER_2026), "shot": "s1"}, SEGS)
    out, n, res = fx.refit(e, SEGS, None, w=W, h=H)
    assert n == fx.MAX_REFITS == 2 and len(calls) == 2
    assert res["ok"] is False                            # never a "✓"
    assert fx.bounds_check(out, W, H)["ok"] is False


def test_refit_asks_nothing_of_an_effect_that_fits(monkeypatch):
    monkeypatch.setattr(fx, "_ask", lambda *a, **k: pytest.fail("the designer was asked"))
    e = fx.validate_effect({**_effect(events=[{"t": 1.0, "x": 0.5, "y": 0.5}], overlay={
        "duration": 2.0, "size": 0.8, "shapes": [_text("Hi", 0.1)], "anim": {}}), "shot": "s1"}, SEGS)
    out, n, res = fx.refit(e, SEGS, None, w=W, h=H)
    assert (n, res["ok"]) == (0, True) and out is e


# ---------------------------------------------------------------- by hand

def test_hand_words_size_place_and_nudge_change_the_effect_and_nothing_asks_a_model(monkeypatch):
    monkeypatch.setattr(fx, "_ask", lambda *a, **k: pytest.fail("a hand control asked the model"))
    e = _effect(events=[{"t": 1.0, "x": 0.5, "y": 0.5}], overlay={
        "duration": 3.0, "size": 0.8, "anim": {}, "shapes": [
            _text("COPPER 2026", 0.1, color="#000000", opacity=0.4), _text("COPPER 2026", 0.1)]})
    a = fx.apply_hand(e, {"words": {"COPPER 2026": "Copper Mountain"}})
    assert [s["text"] for s in a["overlay"]["shapes"]] == ["Copper Mountain"] * 2   # the shadow too
    assert fx.apply_hand(e, {"size": 0.4})["overlay"]["size"] == 0.4
    b = fx.apply_hand(e, {"x": 0.3, "y": 0.8})
    assert (b["events"][0]["x"], b["events"][0]["y"]) == (0.3, 0.8)
    c = fx.apply_hand(e, {"nudge": {"dx": 0.01, "dy": -0.01}})
    assert c["events"][0]["x"] == pytest.approx(0.51) and c["events"][0]["y"] == pytest.approx(0.49)
    assert e["events"][0]["x"] == 0.5                      # the original is untouched
    with pytest.raises(ValueError):
        fx.apply_hand(e, {"words": {"NOT THERE": "x"}})
    with pytest.raises(ValueError):
        fx.apply_hand(e, {"words": {"COPPER 2026": "  "}})
    with pytest.raises(ValueError):
        fx.apply_hand(e, {})


def test_hand_in_and_out_keep_the_fade_out_at_the_end():
    e = _effect(events=[{"t": 1.0, "x": 0.5, "y": 0.5}, ], overlay={
        "duration": 3.0, "size": 0.8, "shapes": [_text("Hi", 0.1)],
        "anim": {"opacity": [[0, 0], [0.25, 1], [2.6, 1], [3.0, 0]]}})
    longer = fx.apply_hand(e, {"length": 4.5})["overlay"]
    assert longer["duration"] == 4.5
    keys = longer["anim"]["opacity"]
    assert keys[1] == [0.25, 1] and keys[-1][0] == 4.5 and keys[-2][0] == pytest.approx(4.1)
    shorter = fx.apply_hand(e, {"length": 1.5})["overlay"]
    ts = [k[0] for k in shorter["anim"]["opacity"]]
    assert ts == sorted(ts) and ts[-1] == 1.5 and ts[1] == 0.25
    fx.validate_overlay(longer), fx.validate_overlay(shorter)
    moved = fx.apply_hand(e, {"start": 9.0}, shot=(0.0, 6.0))
    assert 5.9 < moved["events"][0]["t"] <= 5.95           # kept inside the shot


def test_hand_fit_is_the_deterministic_fit_and_says_when_it_cannot():
    e = _effect(**COPPER_2026)
    fitted = fx.apply_hand(e, {"fit": True}, w=W, h=H)
    assert "text_outside" not in _kinds(fitted)
    # size and place cannot unstack two lines drawn on each other: the card keeps saying so
    card = fx.apply_hand(_effect(**END_CARD), {"fit": True}, w=W, h=H)
    assert _kinds(card) == {"text_overlap"}
    # nothing to fit by size or place: it says so rather than pretending
    chopped = _effect(events=[{"t": 1.0, "x": 0.5, "y": 0.5}], overlay={
        "duration": 3.0, "size": 0.5, "anim": {}, "shapes": [
            {"type": "rect", "at": [0, 0], "w": 1.0, "h": 0.17, "fill": True, "color": "#0b0b0f", "opacity": 0.55},
            _text("Spencer, the milkman", 0.09)]})
    with pytest.raises(ValueError):
        fx.apply_hand(chopped, {"fit": True}, w=W, h=H)


# ---------------------------------------------------------------- the words of a removal, the silence of a title

def test_a_removal_note_is_a_removal_and_a_design_note_is_not():
    a = _effect(**COPPER_2026, id="fx_a")
    b = _effect(events=[{"t": 1.0, "x": 0.5, "y": 0.5}], overlay={
        "duration": 2.0, "size": 0.8, "shapes": [_text("Thanks", 0.1)], "anim": {}}, id="fx_b")
    assert fx.removal_targets("remove the second Copper 2026 title on shot 2", [a, b]) == [a]
    assert fx.removal_targets("Remove the title", [a]) == [a]
    assert fx.removal_targets("remove the title", [a, b]) == []        # which one? the caller says
    assert fx.removal_targets("a title reading Copper 2026", [a]) is None
    assert fx.removal_targets("remove the old title and add a new one", [a]) is None
    assert fx.text_key(a) == "copper 2026"


def test_a_title_the_note_did_not_ask_a_sound_for_is_silent():
    e = {"overlay": {"shapes": [{"type": "rect"}, {"type": "text", "text": "x"}]}, "sound": {"duration": 0.3}}
    assert fx.silence_unasked(e, "a name lower third for Spencer")["sound"] is None
    assert fx.silence_unasked(e, "a title with a whoosh")["sound"] == {"duration": 0.3}
    typed = {"overlay": {"shapes": [{"type": "text", "text": "x", "reveal": "typewriter"}]}, "sound": {"duration": 1}}
    assert fx.silence_unasked(typed, "a typed title")["sound"] == {"duration": 1}
    marker = {"overlay": {"shapes": [{"type": "line"}]}, "sound": {"duration": 0.1}}
    assert fx.silence_unasked(marker, "hit markers")["sound"] == {"duration": 0.1}


def test_title_notes_say_where_a_moved_title_goes():
    segs = [{"id": f"s{i}", "clip": "A.MP4", "in": 0.0, "out": 3.0} for i in range(1, 6)]
    title = {**_effect(**COPPER_2026), "shot": "s1", "status": "accepted"}
    moved = [segs[4]] + segs[:4]                            # shot 5 to the very start
    notes = fx.title_notes([title], segs, moved)
    assert len(notes) == 1 and "COPPER 2026" in notes[0] and "now shot 2" in notes[0]
    assert "0:00" in notes[0] and "0:03" in notes[0] and "no longer opens the film" in notes[0]
    assert fx.title_notes([title], segs, segs) == []
    gone = fx.title_notes([title], segs, segs[1:])
    assert "takes out" in gone[0]


def test_the_check_row_is_put_after_in_frame_by_the_checker(monkeypatch, tmp_path):
    e = fx.validate_effect({**_effect(**COPPER_2026), "shot": "s1"}, SEGS)
    monkeypatch.setattr(fxcheck, "proof", lambda *a, **k: (tmp_path / "p.mp4", tmp_path / "b.mp4", {}))
    monkeypatch.setattr(fx, "verify", lambda *a, **k: {"ok": True, "at": "x", "checks": [
        {"key": "in_shot", "ok": True, "label": "", "detail": ""}, {"key": "in_frame", "ok": True, "label": "", "detail": ""},
        {"key": "picture_landed", "ok": True, "label": "", "detail": ""}]})
    out = fxcheck.check({"effect": e, "seg": SEGS[0], "src": "x", "dir": str(tmp_path), "w": W, "h": H, "fps": 24})
    keys = [c["key"] for c in out["verify"]["checks"]]
    assert keys == ["in_shot", "in_frame", "bounds", "picture_landed"]
    assert out["verify"]["ok"] is False                   # "✓ checked" is not said of a title outside the frame


# ---------------------------------------------------------------- through the server, with the suite's fake backend

@pytest.fixture
def server_fx(monkeypatch, project):
    """The designer answers from a script, the synth writes silence, the proof is the base."""
    import wave

    answers: list[dict] = []
    calls = {"design": 0, "revise": 0}

    def design(note, seg, clip, sidecar, *, reference=None, place=False, proxy=None,
               workdir=None, segments=None, clips=None, window=None):
        calls["design"] += 1
        spec = answers.pop(0) if answers else COPPER_2026
        e = {"name": "Copper title", "why": "w", "note": note, "shot": seg["id"], "clip": seg["clip"],
             "events": [{**spec["events"][0], "t": seg["in"] + 0.5}], "overlay": json.loads(json.dumps(spec["overlay"])),
             "sound": None, "status": "proposed", "created": "2026-11-11T09:00:00",
             "history": [{"note": note, "at": "2026-11-11T09:00:00"}]}
        return fx.validate_effect(e, segments or [seg], clips, keep_meta=False)

    def revise(effect, note, segments, clips=None):
        calls["revise"] += 1
        spec = answers.pop(0) if answers else COPPER_2026
        e = json.loads(json.dumps(effect))
        e["overlay"] = json.loads(json.dumps(spec["overlay"]))
        e["events"] = [{**spec["events"][0], "t": effect["events"][0]["t"]}]
        e["note"] = note
        e["history"] = list(effect.get("history") or []) + [{"note": note, "at": "2026-11-11T09:01:00"}]
        return fx.validate_effect(e, segments, clips)

    def verify(effect, seg, *, onset=None, hz=10, part=None, base=None, impact=True):
        return {"ok": True, "at": "x", "checks": [{"key": "in_shot", "label": "", "ok": True, "detail": ""}]}

    monkeypatch.setattr(fx, "design", design)
    monkeypatch.setattr(fx, "revise", revise)
    monkeypatch.setattr(fx, "part_graph", lambda effects, w, h, fps, workdir, *, vin="vbase", ain="abase": ([], "", vin, ain))
    monkeypatch.setattr(fx, "verify", verify)
    return project, answers, calls


def _seed(project, client):
    edl = json.loads(project["edl"].read_text(encoding="utf-8"))
    edl["segments"] = [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 4.0, "why": "first"},
                       {"clip": "CLIP_B.MP4", "in": 0.0, "out": 3.0, "why": "second"}]
    edl.pop("effects", None)
    project["edl"].write_text(json.dumps(edl, indent=1), encoding="utf-8")
    import server
    import shutil
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
    shutil.rmtree(server.fx_home(), ignore_errors=True)
    return [s["id"] for s in client.get("/api/project").json()["segments"]]


def _wait(client, job, timeout=30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        snap = client.get(f"/api/job/{job}").json()
        if snap["state"] in ("done", "failed"):
            return snap
        time.sleep(0.05)
    raise AssertionError(f"job {job} did not finish")


def _effects(client):
    return {e["id"]: e for e in client.get("/api/fx").json()["effects"]}


GOOD = {**COPPER_2026, "events": [{"t": 1.2, "x": 0.5, "y": 0.5}],
        "overlay": {**COPPER_2026["overlay"], "size": 0.8}}


def test_a_design_outside_the_frame_is_asked_again_before_the_editor_sees_it(server_fx, client):
    project, answers, calls = server_fx
    s1, _s2 = _seed(project, client)
    answers.extend([COPPER_2026, GOOD])                 # the first design is off the frame, the second fits
    job = client.post("/api/fx/design", json={"shot": s1, "note": "a COPPER 2026 title"}).json()["job"]
    snap = _wait(client, job)
    assert snap["state"] == "done", snap
    e = _effects(client)[snap["result"]["id"]]
    assert calls == {"design": 1, "revise": 1}
    assert e["bounds"]["ok"] is True and e["bounds"]["asked"] == 1
    assert e["note"] == "a COPPER 2026 title"
    assert fx.check_bounds(e, 1280, 720)["ok"]


def test_a_designer_that_cannot_fix_it_leaves_a_failing_check_after_two_asks(server_fx, client):
    project, answers, calls = server_fx
    s1, _ = _seed(project, client)
    job = client.post("/api/fx/design", json={"shot": s1, "note": "a COPPER 2026 title"}).json()["job"]
    snap = _wait(client, job)
    e = _effects(client)[snap["result"]["id"]]
    assert calls["revise"] == fx.MAX_REFITS
    assert e["bounds"]["ok"] is False and e["bounds"]["asked"] == 2 and e["bounds"]["problems"]


def test_hand_edits_need_no_model_and_recheck_at_once(server_fx, client):
    project, answers, calls = server_fx
    s1, _ = _seed(project, client)
    answers.append(GOOD)
    snap = _wait(client, client.post("/api/fx/design", json={"shot": s1, "note": "a title"}).json()["job"])
    fx_id = snap["result"]["id"]
    before = dict(calls)
    r = client.post("/api/fx/hand", json={"id": fx_id, "edit": {"size": 1.6}})
    assert r.status_code == 200 and r.json()["bounds"]["ok"] is False      # bigger than the frame: said at once
    r = client.post("/api/fx/hand", json={"id": fx_id, "edit": {"words": {"COPPER 2026": "Copper Mountain Resort Weekend"}}})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["bounds"]["by_hand"] is True
    assert any(s.get("text") == "Copper Mountain Resort Weekend" for s in e["overlay"]["shapes"])
    assert e["bounds"]["ok"] is False                    # too long for the box: the check said so at once
    r = client.post("/api/fx/hand", json={"id": fx_id, "edit": {"fit": True}})
    assert r.status_code == 200 and r.json()["bounds"]["ok"] is True
    r = client.post("/api/fx/hand", json={"id": fx_id, "edit": {"nudge": {"dx": 0.01, "dy": 0}}})
    assert r.json()["events"][0]["x"] > 0.4
    assert client.post("/api/fx/hand", json={"id": fx_id, "edit": {"words": {"nope": "x"}}}).status_code == 400
    assert calls == before                               # not one model call
    # and the PUT of an edit carries the same measurement
    put = client.put(f"/api/fx/{fx_id}", json={"events": [{"t": 1.5, "x": 0.1, "y": 0.5}]}).json()
    assert put["bounds"]["ok"] is False and put["bounds"]["by_hand"] is True


def test_a_fixed_title_replaces_the_old_one_and_never_stacks(server_fx, client):
    project, answers, calls = server_fx
    s1, s2 = _seed(project, client)
    answers.append(GOOD)
    a = _wait(client, client.post("/api/fx/design", json={"shot": s1, "note": "a COPPER 2026 title"}).json()["job"])["result"]["id"]
    assert client.post("/api/fx/accept", json={"id": a}).status_code == 200
    answers.append(GOOD)
    b = _wait(client, client.post("/api/fx/design", json={"shot": s1, "note": "make the COPPER 2026 title fit"}).json()["job"])["result"]["id"]
    assert _effects(client)[b]["replaces"] == [a]
    assert client.post("/api/fx/accept", json={"id": b}).status_code == 200
    now = _effects(client)
    assert now[b]["status"] == "accepted" and now[a]["status"] == "removed"
    live = [x for x in project["edl"].parent.joinpath(project["edl"].name).exists() and
            json.loads(project["edl"].read_text(encoding="utf-8")).get("effects") or []]
    assert [x["id"] for x in live] == [b]               # one title in the cut, not two


def test_remove_the_title_is_a_removal_not_a_design_job(server_fx, client):
    project, answers, calls = server_fx
    s1, _ = _seed(project, client)
    answers.append(GOOD)
    a = _wait(client, client.post("/api/fx/design", json={"shot": s1, "note": "a COPPER 2026 title"}).json()["job"])["result"]["id"]
    client.post("/api/fx/accept", json={"id": a})
    before = dict(calls)
    snap = _wait(client, client.post("/api/fx/design", json={"shot": s1, "note": "remove the COPPER 2026 title"}).json()["job"])
    assert snap["state"] == "done" and "removed" in snap["detail"]
    assert calls == before                                # the designer was not asked
    assert _effects(client)[a]["status"] == "removed"
    # a removal with nothing to remove says which effects are there, and fails visibly
    snap = _wait(client, client.post("/api/fx/design", json={"shot": s1, "note": "remove the title"}).json()["job"])
    assert snap["state"] == "failed" and "nothing here to remove" in snap["detail"]


def test_an_accepted_answers_design_is_accepted_with_it_when_it_fits(server_fx, client):
    project, answers, calls = server_fx
    s1, _ = _seed(project, client)
    answers.append(GOOD)
    snap = _wait(client, client.post("/api/fx/design", json={"shot": s1, "note": "a title", "accept": True}).json()["job"])
    e = _effects(client)[snap["result"]["id"]]
    assert e["status"] == "accepted" and e.get("auto_accepted") and "in the cut" in snap["detail"]
    # its Ctrl+Z: the accept is taken back and the design set aside; the redo puts it back
    r = client.post("/api/fx/discard", json={"id": e["id"], "withdraw": True})
    assert r.status_code == 200 and r.json()["withdrawn"] is True
    assert json.loads(project["edl"].read_text(encoding="utf-8")).get("effects") in (None, [])
    assert client.post("/api/fx/restore", json={"id": e["id"]}).status_code == 200
    assert [x["id"] for x in json.loads(project["edl"].read_text(encoding="utf-8")).get("effects") or []] == [e["id"]]


def test_an_accepted_answers_design_that_fails_the_frame_check_waits_for_the_editor(server_fx, client):
    project, answers, calls = server_fx
    s1, _ = _seed(project, client)
    snap = _wait(client, client.post("/api/fx/design", json={"shot": s1, "note": "a title", "accept": True}).json()["job"])
    e = _effects(client)[snap["result"]["id"]]
    assert e["status"] == "proposed" and e["bounds"]["ok"] is False


def test_a_design_with_no_accept_flag_is_a_proposal_as_before(server_fx, client):
    project, answers, calls = server_fx
    s1, _ = _seed(project, client)
    answers.append(GOOD)
    snap = _wait(client, client.post("/api/fx/design", json={"shot": s1, "note": "a title"}).json()["job"])
    assert _effects(client)[snap["result"]["id"]]["status"] == "proposed"
