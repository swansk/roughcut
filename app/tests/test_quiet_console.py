"""A quiet console on inserts (verdict 3, punch 9; REDESIGN-GOAL dimension 10).

Verdict 3: "15+ console errors per editing session: `400 /api/fx/peaks?shot=tmp-1`,
`400 /api/fx/price?…shot=tmp-1`, `404 /api/lut/tmp-1` for every fresh insert". A shot
put on the timeline has a temporary `tmp-N` id until its save comes back and
`tl.afterSave()` re-keys it; the server has never heard of `tmp-1`, so any per-shot
endpoint asked about it answers 4xx and Chromium logs each one as a console error.
Effects (fx.js) and the grade (grade.js) now wait for the re-key.

Driven in real Chromium on the suite's fixture: three clips inserted, two by `,` from
the Project panel and one by a drag onto the Timeline, each selected and parked on as
it lands, then the page left to settle and save. The network log has no response
>= 400 and the console no error.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_quiet_console.py -q
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

SEED = {"variant": "T", "title": "quiet", "orient": "none", "story": "",
        "target_s": [5, 20],
        # an id in the file, as `server.configure` persists at open: a read mints a
        # fresh one per GET for a segment without, which the server then cannot find
        "segments": [{"id": "quiet00001", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0,
                      "why": "first"}]}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "server did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)
    project["edl"].write_text(original, encoding="utf-8")


def _insert_by_comma(pg, clip: str) -> None:
    pg.locator("#projList tr.row", has_text=clip).click()
    pg.wait_for_function(f"window.rcSource && rcSource.clip === '{clip}.MP4'", timeout=5000)
    pg.evaluate("tl.seek(tl.total())")
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.keyboard.press(",")


def _insert_by_drag(pg, clip: str) -> None:
    dt = pg.evaluate_handle("() => new DataTransfer()")
    pg.locator("#projList tr.row", has_text=clip).dispatch_event("dragstart", {"dataTransfer": dt})
    box = pg.locator("#tl .tl-view").bounding_box()
    at = {"dataTransfer": dt, "clientX": box["x"] + box["width"] - 20,
          "clientY": box["y"] + box["height"] / 2}
    pg.locator("#tl .tl-view").dispatch_event("dragover", at)
    pg.locator("#tl .tl-view").dispatch_event("drop", at)


def test_inserting_three_clips_logs_no_failed_request_and_no_console_error(live_server, project):
    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    bad, console, errors, asked_tmp, asked = [], [], [], [], []
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.on("response", lambda r: bad.append(f"{r.status} {r.request.method} {r.url}")
              if r.status >= 400 else None)
        pg.on("request", lambda r: asked_tmp.append(r.url) if "tmp-" in r.url else None)
        pg.on("request", lambda r: asked.append(r.url))
        pg.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk", timeout=15000)
        pg.wait_for_selector("#projList tr.row", timeout=15000)
        # Effects open, as in the judge's session: its shot follows every selection
        pg.evaluate("dock.open('fx')")
        n0 = pg.evaluate("segs.length")
        for k, (how, clip) in enumerate((("comma", "CLIP_B"), ("drag", "CLIP_C"),
                                         ("comma", "CLIP_A"))):
            if how == "comma":
                _insert_by_comma(pg, clip)
            else:
                _insert_by_drag(pg, clip)
            pg.wait_for_function(f"segs.length === {n0 + k + 1}", timeout=5000)
            # the new shot is selected and the monitor parked on it while its id is
            # still temporary: exactly when the old code asked the server about tmp-N
            pg.wait_for_timeout(900)
        pg.wait_for_function("!saveTimer", timeout=10000)
        # every shot has its server id now, and the tools followed the re-key
        pg.wait_for_function("segs.every(s => s.id && !String(s.id).startsWith('tmp-'))",
                             timeout=10000)
        pg.wait_for_timeout(1500)
        ids = pg.evaluate("segs.map(s => s.id)")
        anchor = pg.evaluate("String(tl.state.anchor)")
        ctx.close()
        b.close()
    assert len(ids) == n0 + 3, ids
    assert asked_tmp == [], asked_tmp
    assert bad == [], bad
    assert console == [], console
    assert errors == [], errors
    # ...and Effects did ask about the selected shot once it had its real id
    assert anchor in ids and not anchor.startswith("tmp-"), (anchor, ids)
    assert any(f"/api/fx/price?place=1&shot={anchor}" in u for u in asked), anchor
