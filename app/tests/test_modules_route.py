"""The workspace's modules are served by name from a fixed list (redesign wave 2)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_listed_modules_are_served_with_their_type():
    c = TestClient(server.app)
    for name in ("firstpass.js", "export.js", "firstpass.css", "export.css", "meters.js", "meters.css"):
        r = c.get(f"/modules/{name}")
        assert r.status_code == 200, name
        assert r.headers["content-type"].startswith(
            "text/css" if name.endswith(".css") else "application/javascript"), name


def test_anything_off_the_list_is_404():
    c = TestClient(server.app)
    for name in ("server.py", "app.js", "..%2Fserver.py", "index.html", "nope.js"):
        assert c.get(f"/modules/{name}").status_code == 404, name


def test_the_board_loads_its_modules():
    html = (Path(server.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    for tag in ('src="/modules/firstpass.js"', 'src="/modules/export.js"', 'src="/modules/meters.js"', 'id="meters"',
                'id="firstpass"', 'id="exportDialog"'):
        assert tag in html, tag
