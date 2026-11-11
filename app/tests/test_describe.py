"""Describe the footage as it is, and let the editor read it (wave 23, lane DESCRIBE).

Copper's editors (judge/COPPER-EDITORS.md area 6): the look told the model it was on "an action
camera worn on the skier's helmet or chest", ranked events first and called "nothing notable" a
good answer, so a travel day came back as verdicts ("not ski footage, no events"). Here:

  * the look prompt (no camera assumption, a description first, events kept as strict as ever),
    its fields (`description`, `events`, `summary` kept) and its version;
  * what the index found, readable (GET /api/found) and a description the editor writes sticking;
  * Find: a clip name on every hit's data, a floor, negation, a spoken line over a black frame.

No live calls: the model is the suite's scripted backend or no backend at all.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from roughcut import find  # noqa: E402


@pytest.fixture(scope="module")
def vp():
    spec = importlib.util.spec_from_file_location(
        "visual_pass", ROOT / "research" / "tools" / "visual_pass.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ the look prompt

def _prompt(vp, template=None):
    return (template or vp.PROMPT).format(n=8, clip="GX010490.MP4", interval=4.0, start=0, end=28,
                                          stamps="0, 4, 8")


def test_the_look_assumes_no_camera_and_writes_no_verdicts(vp):
    text = vp.SYSTEM + _prompt(vp) + _prompt(vp, vp.FINE_PROMPT)
    for phrase in ("worn on the skier", "pointing where they look", "wearer is never in frame",
                   '"nothing notable" is a good', "is a correct answer. Say so"):
        assert phrase not in text, phrase
    # the description is asked for first and in the editor's order
    p = _prompt(vp)
    assert p.index("`description`") < p.index("`moments`") or p.index("`description`") < p.index("Moments")
    for part in ("who", "where", "what happens"):
        assert part in p.lower()
    # and verdicts are named as forbidden, so the model knows
    assert "never \"nothing notable\"" in p and "single frame" in p


def test_the_event_rules_are_as_strict_as_before(vp):
    """R10 / R12 tuned these for recall on Killington: they must all still be in the prompt."""
    p = " ".join(_prompt(vp).split())
    for rule in ("needs **another person** visibly in the air",
                 "camera rolls, not the rider",
                 "40-45", "find the snow",
                 "under two seconds", "one frame, at most two",
                 "A person standing, walking, or hiking is `action` with `notable: false`",
                 "*appears*, *seems*, *suggests*", "`confidence` is `high` only when",
                 "pov-gear"):
        assert rule in p, rule
    assert "EVENT_KINDS" not in p
    assert vp.EVENT_KINDS == ("jump", "fall", "crash") and vp.MAX_EVENT_FRAMES == 2
    # the close look keeps its own
    f = " ".join(_prompt(vp, vp.FINE_PROMPT).split())
    assert "camera rolls, not the rider" in f and "tightest start and end" in f


def test_a_planted_camera_is_not_told_it_rolls(vp):
    assert "planted or hand-held" in " ".join(_prompt(vp).split())


def test_the_sidecar_is_versioned_and_keeps_the_old_field(vp):
    assert vp.PROMPT_VERSION >= 4
    out = vp.validate({"moments": [], "unusable": [], "description": "Two friends stand at a counter."},
                      0.0, 28.0, [0.0, 4.0])
    assert out["description"] == out["summary"] == "Two friends stand at a counter."
    assert out["events"] == []
    # an old reply, or a model that still says summary: read either way
    legacy = vp.validate({"moments": [], "unusable": [], "summary": "A clip of a lift."}, 0.0, 28.0, [0.0])
    assert legacy["description"] == "A clip of a lift."
    assert vp.description_of({"summary": "old words"}) == "old words"
    assert vp.description_of({"summary": "old", "description": "new"}) == "new"


def test_verdict_sentences_are_dropped_and_counted(vp):
    text = ("A man in a teal jacket stands by a white van at night. Nothing notable happens. "
            "This is not ski footage. There are no jumps, falls or crashes. He smiles at the lens.")
    out = vp.validate({"moments": [], "unusable": [], "description": text}, 0.0, 8.0, [0.0])
    assert out["description"] == "A man in a teal jacket stands by a white van at night. He smiles at the lens."
    assert out["verdicts_dropped"] == 3
    # a description that is only verdicts comes back empty rather than as a verdict
    assert vp.validate({"moments": [], "unusable": [], "description": "Nothing notable happens."},
                       0.0, 8.0, [0.0])["description"] == ""


def test_events_are_a_field_of_their_own_and_demotion_is_unchanged(vp):
    ok = {"start": 20.0, "end": 20.0, "what": "skier at 20.0 mid-air, skis clear of the snow",
          "kind": "jump", "frames": [20.0], "confidence": "high", "notable": True}
    hedged = dict(ok, start=4.0, end=4.0, frames=[4.0], what="a skier appears to be airborne")
    out = vp.validate({"moments": [ok, hedged], "unusable": [], "description": "x"}, 0.0, 28.0,
                      [0.0, 4.0, 8.0, 12.0, 16.0, 20.0, 24.0])
    assert [e["start"] for e in out["events"]] == [20.0]         # only the claim that held
    by = {m["start"]: m for m in out["moments"]}
    assert len(by) == 2 and by[4.0]["notable"] is False and "hedged" in by[4.0]["demoted"]


def test_read_sheet_carries_the_new_fields_through(vp, monkeypatch, tmp_path):
    from roughcut import inference
    seen = {}

    class R:
        content = None
        input_tokens, output_tokens, projected_usd, model, latency_ms = 1, 1, 0.0, "m", 1

    def fake(prompt, **kw):
        seen["prompt"], seen["system"] = prompt, kw["system"]
        r = R()
        r.content = kw["validate"]({"moments": [], "unusable": [],
                                    "description": "A skier in jean shorts follows a friend."})
        return r

    monkeypatch.setattr(inference, "complete", fake)
    sheet = tmp_path / "s.jpg"
    sheet.write_bytes(b"x")
    got = vp.read_sheet(sheet, "GX010487.MP4", [{"t": 0.0}, {"t": 4.0}], 4.0, "analysis")
    assert got["description"].startswith("A skier in jean shorts")
    assert got["summary"] == got["description"] and got["events"] == []
    assert "action camera worn on the skier" not in seen["system"]


# ------------------------------------------------------------------ Find: floor, negation, order

def _clips(**extra):
    base = {
        "GX1.MP4": {"clip": "GX1.MP4", "duration": 40.0, "summary": {},
                    "transcript": [{"start": 0.0, "end": 6.0, "text": "he is on the porcelain throne"}],
                    "visual": {"moments": [], "unusable": []}},
        "GX2.MP4": {"clip": "GX2.MP4", "duration": 30.0, "summary": {}, "transcript": [],
                    "visual": {"moments": [{"start": 0, "end": 30, "kind": "action", "notable": False,
                                            "what": "snow slope, a skier goes by, snow all around"}],
                               "unusable": [{"start": 0.0, "end": 30.0, "why": "black"}]}},
        "GX3.MP4": {"clip": "GX3.MP4", "duration": 30.0, "summary": {}, "transcript": [],
                    "visual": {"moments": [{"start": 2, "end": 8, "kind": "action", "notable": False,
                                            "what": "snow slope, a skier goes by"}], "unusable": []}},
        "GX4.MP4": {"clip": "GX4.MP4", "duration": 30.0, "summary": {}, "transcript": [],
                    "visual": {"moments": [{"start": 2, "end": 8, "kind": "scenery", "notable": False,
                                            "what": "snow slope under a chairlift"}], "unusable": []}},
    }
    base.update(extra)
    return base


def _search(query, clips, rows=None, **kw):
    def local(text, k=3, **_):
        return {"ok": True, "why": "", "results": {text: rows or []}}
    return find.search(query, clips, local_search=local, **kw)


def test_a_denied_word_is_not_a_match():
    assert find._affirmed_tokens("no jumps, falls or crashes are visible") == ["visible"]
    assert "fall" not in find._affirmed_tokens("This is the camera being moved, not a fall")
    assert find._affirmed_tokens("A man did not fall but jumped") == ["man", "jumped"]
    clips = _clips()
    clips["GX3.MP4"]["visual"]["moments"][0]["what"] = "the camera is moved, not a fall"
    assert find.lexical("someone wipes out and falls", clips) == []
    # speech is not read that way: "no way, I fell in" is a fall
    clips["GX1.MP4"]["transcript"] = [{"start": 1, "end": 3, "text": "no way, I fell in the stream"}]
    assert find.lexical("fell stream", clips)


def test_a_word_in_every_clip_says_nothing_about_which_moment():
    """"a dog in the snow" matched 13 moments, every one by "snow"."""
    clips = _clips()
    w = find._weights(["dog", "snow"], clips)
    assert w["dog"] > 0.9 and w["snow"] <= 0.5
    rows = find.lexical("a dog in the snow", clips, floor=find.LEXICAL_FLOOR)
    assert rows == []
    assert find.lexical("a dog in the snow", clips)          # without the floor they are still the weak guesses


def test_nothing_under_the_floor_says_so_and_counts_what_it_held_back():
    clips = _clips()
    got = _search("a dog in the snow", clips,
                  [{"clip": "GX3", "start": 1.0, "end": 3.0, "score": 0.165}])
    assert got["matches"] == []
    assert got["floor"]["hidden"] >= 2 and got["floor"]["local"] == find.LOCAL_FLOOR
    weak = _search("a dog in the snow", clips,
                   [{"clip": "GX3", "start": 1.0, "end": 3.0, "score": 0.165}], weak=True)
    assert weak["matches"] and weak["floor"]["weak"] is True


def test_a_real_picture_match_clears_the_floor():
    got = _search("chairlift", _clips(), [{"clip": "GX4", "start": 3.0, "end": 5.0, "score": 0.24}])
    assert got["matches"] and got["matches"][0]["clip"] == "GX4.MP4"


def test_a_picture_match_in_a_black_stretch_is_dropped():
    got = _search("hot tub", _clips(), [{"clip": "GX2", "start": 1.0, "end": 3.0, "score": 0.30}])
    assert got["matches"] == []                  # a black frame looks like everything
    weak = _search("hot tub", _clips(), [{"clip": "GX2", "start": 1.0, "end": 3.0, "score": 0.30}], weak=True)
    assert weak["matches"] == []                 # not even on request: it shows nothing


def test_a_spoken_phrase_outranks_a_look_alike_frame():
    """"porcelain throne": the line in GX1 was #2 under a near-black frame in GX2 (picture
    matches carried no words and tied the spoken hit on rank)."""
    clips = _clips()
    clips["GX2.MP4"]["visual"]["unusable"] = []          # a dim frame the look did not call unusable
    got = _search("porcelain throne", clips, [{"clip": "GX2", "start": 26.0, "end": 28.0, "score": 0.30}])
    assert got["matches"][0]["clip"] == "GX1.MP4"
    assert got["matches"][0]["source"].startswith("heard")


def test_an_unchecked_claim_is_findable_but_labelled():
    """The one clear jump on Copper was held back by the checker and so never offered to Find."""
    clips = _clips()
    clips["GX3.MP4"]["visual"]["unsupported"] = [
        {"start": 19.4, "end": 20.4, "kind": "jump", "notable": False,
         "what": "A skier in shorts comes over the rise, skis clear of the snow"}]
    rows = find.lexical("someone jumps off a kicker", clips)
    assert rows and rows[0]["clip"] == "GX3.MP4" and rows[0]["start"] == pytest.approx(19.4)
    assert "not checked" in rows[0]["why"]


def test_what_the_clip_is_finds_it_and_an_editors_words_count():
    clips = _clips()
    clips["GX4.MP4"]["visual"]["moments"] = []
    clips["GX4.MP4"]["visual"]["description"] = "Two friends ride a chairlift, the bar down."
    clips["GX4.MP4"]["visual"]["description_by"] = "editor"
    rows = find.lexical("riding the chairlift", clips)
    assert rows and rows[0]["clip"] == "GX4.MP4" and rows[0]["source"] == "described"
    assert "yours" in rows[0]["why"] and rows[0]["end"] == pytest.approx(30.0)


# ------------------------------------------------------------------ the endpoints

def _visual_dir(project):
    d = project["work"] / "visual-describe"
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture
def described(project):
    """The suite's bin with a visual sidecar on CLIP_A: an old one (summary only)."""
    import server
    from fastapi.testclient import TestClient
    vis = _visual_dir(project)
    for f in vis.glob("*"):
        f.unlink()
    (vis / "CLIP_A.visual.json").write_text(json.dumps({
        "clip": "CLIP_A.MP4", "summary": "This is not skiing footage. Nothing notable happens.",
        "moments": [{"start": 1.0, "end": 2.0, "what": "a man laughs", "kind": "reaction",
                     "notable": True, "confidence": "high", "frames": [1.0]}],
        "unusable": [], "frames_sampled": [0.0, 4.0, 8.0],
        "params": {"prompt_version": 3, "interval_s": 4.0}}), encoding="utf-8")
    original = project["edl"].read_text(encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"], project["work"],
                     proxies=False, visual=vis, assets=project["assets"])
    with TestClient(server.app) as c:
        yield c, vis
    project["edl"].write_text(original, encoding="utf-8")
    for f in vis.glob("*"):
        f.unlink()


def test_what_the_index_found_is_readable(described):
    c, _ = described
    d = c.get("/api/found/CLIP_A.MP4").json()
    assert d["heard"] and d["n_words"] == 6
    assert [(u["start"], u["text"]) for u in d["transcript"]] == [
        (0.5, "hello there"), (2.4, "how are you"), (5.0, "goodbye")]
    assert d["looked"] and d["description_by"] == "model"
    assert d["description"].startswith("This is not skiing")        # an old sidecar still reads
    assert [m["what"] for m in d["moments"]] == ["a man laughs"]
    assert d["basis"]["frames"] == 3 and d["basis"]["interval_s"] == 4.0   # which frames it rests on
    assert c.get("/api/found/CLIP_NOPE.MP4").status_code == 404


def test_an_unlooked_clip_reads_as_not_looked_at(described):
    c, _ = described
    d = c.get("/api/found/CLIP_B.MP4").json()
    assert d["looked"] is False and d["description"] == "" and d["transcript"]


def test_a_description_the_editor_writes_sticks_and_is_theirs(described):
    c, vis = described
    r = c.put("/api/found/CLIP_A.MP4/description", json={"text": "Two friends laugh on the plane."})
    assert r.status_code == 200
    d = r.json()
    assert d["description"] == "Two friends laugh on the plane." and d["description_by"] == "editor"
    # everywhere it is read: the clip list's Description (visual.summary), the Ask's inventory
    clip = c.get("/api/project").json()["clips"]["CLIP_A.MP4"]
    assert clip["visual"]["summary"] == "Two friends laugh on the plane."
    assert clip["visual"]["description_by"] == "editor"
    # and a re-look (the sidecar rewritten by the tool) does not replace it
    side = vis / "CLIP_A.visual.json"
    j = json.loads(side.read_text())
    j.update(description="A new look, new words.", summary="A new look, new words.")
    j["params"]["prompt_version"] = 4
    side.write_text(json.dumps(j))
    assert c.get("/api/found/CLIP_A.MP4").json()["description"] == "Two friends laugh on the plane."
    # an empty text gives the look's words back
    back = c.put("/api/found/CLIP_A.MP4/description", json={"text": "  "}).json()
    assert back["description"] == "A new look, new words." and back["description_by"] == "model"
    assert not (vis / "CLIP_A.mine.json").exists()


def test_a_description_can_be_written_for_a_clip_never_looked_at(described):
    c, _ = described
    d = c.put("/api/found/CLIP_C.MP4/description", json={"text": "The lodge, from the car park."}).json()
    assert d["description"] == "The lodge, from the car park." and d["description_by"] == "editor"


def test_a_runaway_description_is_refused(described):
    c, _ = described
    assert c.put("/api/found/CLIP_A.MP4/description", json={"text": "x" * 2000}).status_code == 400
    assert c.put("/api/found/CLIP_NOPE.MP4/description", json={"text": "x"}).status_code == 404


def test_find_names_its_floor_and_takes_weak(described):
    c, _ = described
    r = c.post("/api/find", json={"query": "hello", "local": False}).json()
    assert r["floor"]["lexical"] == find.LEXICAL_FLOOR and r["matches"]
    none = c.post("/api/find", json={"query": "porcupine", "local": False}).json()
    assert none["matches"] == [] and none["floor"]["hidden"] == 0
