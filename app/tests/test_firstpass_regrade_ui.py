"""A re-run can still re-cut after the AI regrades the film (verdict 11, punch 6), in a
real browser.

Verdict 11: after the AI colour-matched every shot (one `match` op, accepted in the
Assistant), the re-run panel said "22 shots edited by hand since … may replace 0": the
AI's grade counted as a hand edit and locked every shot in place. A grade or an effect,
the AI's or the editor's, now rides on whatever shot the re-run keeps and never locks
one; only a cut made by hand does.

On the suite's footage, every model call scripted (the index runs with test_index's
stubbed tools): a first pass of three shots, accepted; a Lumetri value set by hand on
shot 1; the AI's Color Match of every other shot to shot 1, reviewed and accepted as the
editor does it. The pass is not stale, and the panel says the re-run keeps no shot you
cut by hand, keeps the grade on every shot it keeps, and may replace all three. One hand
trim locks that one shot. The re-run replaces a graded shot and keeps the others, and
every kept shot keeps its grade on disk.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import config, inference  # noqa: E402
from test_firstpass_ui import (PANEL, RUN, _accept, _board_settled,  # noqa: E402,F401
                               _label, _one_click_to_panel, _segs, _state, live, page)

# On the fixture's 24 fps grid, so a proposal's snap moves none of them.
FIRST = [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "the greeting"},
         {"clip": "CLIP_B.MP4", "in": 2.5, "out": 4.0, "why": "the reply"},
         {"clip": "CLIP_C.MP4", "in": 0.5, "out": 2.5, "why": "the walk"}]
# The re-run: shot 1 as the pass first had it (the hand trim is put back by the lock),
# shot 2 as it is, and shot 3 replaced by a later stretch of its clip.
RECUT = [dict(FIRST[0]), dict(FIRST[1]),
         {"clip": "CLIP_C.MP4", "in": 3.0, "out": 5.0, "why": "the walk, later"}]
ASK = "Make every shot match the colour of the first shot"


class Regrade:
    """The first pass's cut, the AI prompt's answer (`ops`), and a re-run's cut."""

    name = "regrade-scripted"

    def __init__(self):
        self.calls: list[str] = []
        self.ops: list[dict] = []
        self.hold = None                       # test_firstpass_ui's fixtures read it

    def complete(self, request):
        prompt, schema = request.prompt or "", request.schema or {}
        if "kind" in schema and "ops" in schema:
            kind = "assist"
            text = json.dumps({"kind": "ops", "summary": "every shot matched to the first",
                               "ops": self.ops})
        elif "segments" not in schema:
            kind, text = "other", "{}"
        elif "Re-run the first pass" in prompt:
            kind, text = "rerun", json.dumps({"segments": RECUT, "notes": "a re-cut"})
        else:
            kind, text = "run", json.dumps({"segments": FIRST, "notes": "a conversation"})
        self.calls.append(kind)
        model = config.model_for(request.role)
        return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                backend=self.name, model=model, projected_usd=1e-4,
                                latency_ms=1, raw=text)


@pytest.fixture
def model():
    m = Regrade()
    inference.set_backend(m)
    inference.reset_spend()
    yield m
    inference.set_backend(None)


def _disk(live) -> dict:
    return httpx.get(live + "/api/project", timeout=20).json()


def _ai_accept(pg, model, ops) -> None:
    """The AI prompt's answer, reviewed in the Assistant and accepted as the editor does
    it (Accept all), then on disk."""
    model.ops = ops
    res = pg.evaluate("""async (text) => {
      await roughcutFlush();
      const r = await (await fetch('/api/assist', {method: 'POST',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({text, scope: {kind: 'sequence', playhead: tl.state.playhead}})})).json();
      for (;;) {
        const d = await (await fetch('/api/assist/' + r.job)).json();
        if (d.status !== 'running') return d;
        await new Promise((z) => setTimeout(z, 50));
      }
    }""", ASK)
    assert res["status"] == "done", res
    pg.evaluate("(r) => rcActions.review(r)", res)
    pg.wait_for_selector("[data-testid=ai-review] [data-testid=ai-op-row]")
    pg.locator("[data-testid=ai-accept-all]").click()
    pg.wait_for_function("!rcActions.open")
    pg.evaluate("""async () => {
      for (let k = 0; k < 3; k++) {
        await roughcutFlush();
        if (tl.saveSeq) await tl.saveSeq();
        await new Promise((r) => setTimeout(r, 60));
      }
    }""")


def test_an_ai_color_match_over_the_film_leaves_the_rerun_free_to_recut(page, live, model):
    import server

    pg = page
    for c in ("CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"):
        assert server.measure_colour(c), c          # the match's preview reads each clip

    # ---- a first pass, accepted: three shots, the machine's
    _one_click_to_panel(pg)
    pg.locator(RUN).click()
    segs = _accept(pg, live, 3)
    a, b, c = (s["id"] for s in segs)

    # ---- a Lumetri value by hand on shot 1, then the AI's Color Match over the film
    assert pg.evaluate("(id) => effects.set('saturation', 60, tl.byId(id))", a)
    _ai_accept(pg, model, [{"op": "match", "ids": [b, c], "to": "reference", "reference": a}])
    disk = _disk(live)
    col = disk["colour"]
    assert col.get("reference") == a and all(col["shots"][x]["match"] == "reference"
                                             for x in (b, c)), col
    graded = {s["id"]: s.get("controls") for s in disk["segments"]}
    assert graded[a] == {"saturation": 60.0}, graded

    # ---- the grade locks nothing: not stale, and the panel says the re-run may recut
    s = _state(live)
    assert s["manual_edits"] == [] and s["state"] == "done", s
    assert [r["id"] for r in s["would"]["replace"]] == [a, b, c]
    pg.evaluate("rcFirstPass.refresh()")
    _label(pg, "Re-run first pass")
    assert not pg.locator("#firstpass .fp-dot").is_visible(), "a grade is not an edit of the cut"
    text = _one_click_to_panel(pg)
    assert ("Keeps 0 shots you cut by hand · keeps the grade on every shot it keeps"
            " · may replace 3") in text, text
    assert "edited by hand" not in text, text
    pg.keyboard.press("Escape")

    # ---- one hand trim: that shot, and only that one, is locked
    _board_settled(pg)
    trimmed = dict(next(x for x in disk["segments"] if x["id"] == a), out=1.75)
    body = [trimmed] + [x for x in disk["segments"] if x["id"] != a]
    r = httpx.put(live + "/api/project", json={"segments": body, "story": ""}, timeout=20)
    assert r.status_code == 200, r.text
    assert [(m["id"], m["how"]) for m in _state(live)["manual_edits"]] == [(a, ["trim"])]
    pg.reload()
    pg.wait_for_function("window.rcFirstPass && rcFirstPass.state()", timeout=20000)
    _label(pg, "Re-run first pass")
    text = _one_click_to_panel(pg)
    assert ("Keeps 1 shot you cut by hand · keeps the grade on every shot it keeps"
            " · may replace 2") in text, text
    kept = pg.locator('[data-testid="firstpass-kept"] li')
    assert kept.count() == 1 and kept.first.get_attribute("data-shot") == a
    assert kept.first.inner_text().endswith("· trimmed"), kept.first.inner_text()

    # ---- the re-run: shot 3 (graded by the AI) replaced; the kept shots keep their grade
    before = {x["id"]: x for x in _disk(live)["segments"]}
    pg.locator(RUN).click()
    pg.wait_for_selector("#proposal", state="visible", timeout=90000)
    counts = pg.evaluate("pendingPlan.firstpass.diff.counts")
    assert (counts["locked"], counts["kept"], counts["replaced"], counts["new"]) == (1, 2, 1, 1), counts
    after = _accept(pg, live, 3)
    assert "rerun" in model.calls
    by = {x["id"]: x for x in after}
    for sid in (a, b):
        assert sid in by, (sid, after)
        assert ((by[sid]["clip"], by[sid]["in"], by[sid]["out"])
                == (before[sid]["clip"], before[sid]["in"], before[sid]["out"])), (sid, by[sid])
    assert (by[a]["in"], by[a]["out"]) == (trimmed["in"], 1.75), "the hand trim comes back exactly"
    assert c not in by, "the graded shot 3 was replaced"
    new = [x for x in after if x["id"] not in (a, b)]
    # the later stretch of CLIP_C (its in snapped to speech by the proposal's polish)
    assert [x["clip"] for x in new] == ["CLIP_C.MP4"] and new[0]["out"] == 5.0, new
    disk = _disk(live)
    col = disk["colour"]
    assert col.get("reference") == a and col["shots"][b]["match"] == "reference", col
    assert {x["id"]: x.get("controls") for x in disk["segments"]}[a] == {"saturation": 60.0}
    deadline = time.time() + 10
    while time.time() < deadline and _state(live)["state"] != "done":
        time.sleep(0.2)
    s = _state(live)
    assert s["state"] == "done" and [(m["id"], m["how"]) for m in s["manual_edits"]] == [
        (a, ["trim"])], s


def test_an_accepted_ai_trim_and_speed_change_read_edited_by_the_ai(page, live, model):
    """Verdict 12, punch 10: "an AI-made trim and speed change are listed as 'cut by hand'"
    in the re-run panel. The AI's cut edits, reviewed and accepted, still lock their shots
    (a cut is a cut), and the words say who made them: the kept line counts them apart,
    each row says "… by the AI", the checkbox and the stale line too. A hand trim on
    another shot still reads "cut by hand"."""
    pg = page
    # ---- a first pass, accepted: three shots, the machine's
    _one_click_to_panel(pg)
    pg.locator(RUN).click()
    segs = _accept(pg, live, 3)
    a, b, c = (s["id"] for s in segs)
    pg.keyboard.press("Escape")
    # ---- the AI trims shot 2 and slows shot 3; the editor accepts both in the review
    _ai_accept(pg, model, [{"op": "trim", "id": b, "in": 2.75},
                           {"op": "speed", "id": c, "rate": 0.5}])
    disk = {x["id"]: x for x in _disk(live)["segments"]}
    assert disk[b]["in"] == 2.75 and disk[c].get("speed") == 0.5, disk
    # ---- and a hand trim on shot 1
    _board_settled(pg)
    body = [dict(x, out=1.75) if x["id"] == a else x for x in _disk(live)["segments"]]
    r = httpx.put(live + "/api/project", json={"segments": body, "story": ""}, timeout=20)
    assert r.status_code == 200, r.text
    rows = {m["id"]: (m["how"], m["by"]) for m in _state(live)["manual_edits"]}
    assert rows == {a: (["trim"], "hand"), b: (["trim"], "ai"), c: (["speed"], "ai")}, rows
    s = _state(live)
    assert s["state"] == "stale-after-edits", s
    assert s["stale"][0] == "1 shot cut by hand and 2 shots edited by the AI since", s["stale"]
    # ---- the panel: who cut each kept shot
    pg.reload()
    pg.wait_for_function("window.rcFirstPass && rcFirstPass.state()", timeout=20000)
    _label(pg, "Re-run first pass")
    text = _one_click_to_panel(pg)
    assert ("Keeps 3 edited shots (1 cut by hand, 2 by the AI) · keeps the grade on every shot"
            " it keeps · may replace 0") in text, text
    assert "1 shot cut by hand and 2 shots edited by the AI since" in text, text
    kept = {li.get_attribute("data-shot"): li.inner_text()
            for li in pg.locator('[data-testid="firstpass-kept"] li').all()}
    assert kept[a].endswith("· trimmed") and kept[b].endswith("· trimmed by the AI") \
        and kept[c].endswith("· speed changed by the AI"), kept
    assert "keep the edited shots, exactly" in text, text
    # unticked: none of them kept, said without "your hand cuts"
    pg.locator('[data-fp="keepmine"]').click()
    pg.wait_for_function("(document.querySelector('[data-testid=\"firstpass-panel\"]') || {})"
                         ".innerText.includes('Keeps none of the edited shots')", timeout=5000)
    assert "may replace all 3" in pg.locator(PANEL).inner_text()
    # ---- only the AI's: the line says so in its words
    pg.locator('[data-fp="keepmine"]').click()
    pg.keyboard.press("Escape")
    _board_settled(pg)
    first = next(x for x in segs if x["id"] == a)                     # shot 1 as accepted
    body = [dict(x, out=first["out"]) if x["id"] == a else x for x in _disk(live)["segments"]]
    assert httpx.put(live + "/api/project", json={"segments": body, "story": ""}, timeout=20).status_code == 200
    assert {m["id"]: m["by"] for m in _state(live)["manual_edits"]} == {b: "ai", c: "ai"}
    pg.reload()
    pg.wait_for_function("window.rcFirstPass && rcFirstPass.state()", timeout=20000)
    _label(pg, "Re-run first pass")
    text = _one_click_to_panel(pg)
    assert "Keeps 2 shots edited by the AI · keeps the grade on every shot it keeps · may replace 1" \
        in text, text
    assert "cut by hand" not in text, text
