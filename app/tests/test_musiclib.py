"""The shared music library (2026-11-14): search by mood and length, ids the AI can name,
credits, and resolve() finding a fetched file. Offline: a fake catalogue, no download."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from roughcut import actions, assist, music, musiclib  # noqa: E402

PIECES = [
    {"title": "Silly Walk", "filename": "Silly Walk.mp3", "length": "00:02:30", "genre": "20",
     "bpm": "140", "feel": "Bouncy, Humorous", "description": "A goofy march.", "instruments": "Tuba"},
    {"title": "Short Gag", "filename": "Short Gag.mp3", "length": "00:00:40", "genre": "20",
     "bpm": "140", "feel": "Bouncy, Humorous", "description": "Too short for a film.", "instruments": "Kazoo"},
    {"title": "Dark Hall", "filename": "Dark Hall.mp3", "length": "00:03:00", "genre": "10",
     "bpm": "60", "feel": "Dark, Eerie", "description": "Spooky.", "instruments": "Organ"},
    {"title": "Sunny Road", "filename": "Sunny Road.mp3", "length": "00:03:10", "genre": "16",
     "bpm": "120", "feel": "Bright, Uplifting", "description": "A happy drive.", "instruments": "Guitar"},
    {"title": "Big Drums", "filename": "Big Drums.mp3", "length": "00:03:00", "genre": "22",
     "bpm": "100", "feel": "Epic, Driving, Action", "description": "Huge.", "instruments": "Drums"},
]


def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUGHCUT_MUSIC_LIBRARY", str(tmp_path))
    (tmp_path / "catalog.json").write_text(json.dumps(
        {"genres": {"20": "Silent Film Score", "10": "Horror", "16": "Pop", "22": "Soundtrack"},
         "pieces": PIECES}), encoding="utf-8")
    musiclib._CACHE.clear()
    return tmp_path


def state(**kw):
    s = {"segments": [{"id": "s1", "clip": "a.mp4", "in": 0, "out": 90}], "clips": {"a.mp4": {"duration": 100}},
         "fps": 24.0, "music": None, "music_tracks": []}
    s.update(kw)
    return s


def test_search_fits_mood_and_length(tmp_path, monkeypatch):
    lib(tmp_path, monkeypatch)
    ids = [t["id"] for t in musiclib.search("make it a comical travel video", 90)]
    assert ids[0] == "silly-walk"
    assert ids.index("short-gag") > ids.index("silly-walk")      # 40 s cannot carry a 90 s film
    assert ids[-1] == "dark-hall"                                   # never asked for
    assert musiclib.search("epic hype edit", 90)[0]["id"] == "big-drums"
    assert musiclib.search("add music that fits", 90)[0]["id"] == "sunny-road"   # no mood: a trip film


def test_ai_can_name_a_library_track(tmp_path, monkeypatch):
    lib(tmp_path, monkeypatch)
    ok, bad = actions.validate([{"op": "music", "track": "library/silly-walk", "level": -8}], state())
    assert not bad and ok[0]["track"] == "library/silly-walk"
    ok, bad = actions.validate([{"op": "music", "track": "library/nope"}], state())
    assert bad and "library/<id>" in bad[0]["why"]


def test_prompt_lists_best_fits(tmp_path, monkeypatch):
    lib(tmp_path, monkeypatch)
    block = assist.music_library_block("something goofy", state())
    assert "library/silly-walk" in block and "Humorous" in block and "90 s" in block
    monkeypatch.setenv("ROUGHCUT_MUSIC_LIBRARY", str(tmp_path / "none"))
    assert assist.music_library_block("something goofy", state()) == ""


def test_resolve_and_credit(tmp_path, monkeypatch):
    lib(tmp_path, monkeypatch)
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "Silly Walk.mp3").write_bytes(b"ID3" + b"\0" * 64)
    p = music.resolve("library/silly-walk", tmp_path, "bin", None)
    assert p.name == "Silly Walk.mp3"
    c = musiclib.credit_for("library/silly-walk")
    assert "Silly Walk" in c and "Kevin MacLeod" in c and "By Attribution 4.0" in c
    assert musiclib.credit_for("project/x.mp3") is None


def test_missing_file_offline_is_a_plain_error(tmp_path, monkeypatch):
    lib(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise OSError("network unreachable")
    monkeypatch.setattr(musiclib, "_get", boom)
    try:
        music.resolve("library/sunny-road", tmp_path, "bin", None)
    except FileNotFoundError as exc:
        assert "online" in str(exc)
    else:
        raise AssertionError("an offline fetch must fail in words")
