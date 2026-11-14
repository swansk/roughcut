"""A music library the AI can choose from (2026-11-14, Karl: "a music library the AI can pick from").

Until now the AI could only pick a track the editor had already brought in (Add music…), so
"add music that fits" on a new project answered "the project has no music". This is a shared
library of about 1,440 tracks by Kevin MacLeod (incompetech.com), each with a mood ("feel"),
tempo, length, instruments and a line about it, free to use with a credit (CC BY 4.0). The
site publishes its catalogue for agents (https://incompetech.com/llms.txt).

- The **catalogue** (metadata only, ~0.9 MB) lives at `<library>/catalog.json`.
- A **track's file** is fetched the first time a cut uses it, into `<library>/files/`, and kept.
  `fetch --starter` pre-fetches a spread of moods so the board works offline.
- A cut names a library track as `library/<id>` in `effects_music.asset`; `music.resolve()`
  hands that to `path()`, which fetches it if it is not here yet.
- The AI sees the dozen tracks that best fit the words it was given and the film's length
  (`search()`), never the whole catalogue.
- Every export that uses a library track carries its credit (`credit()`): in the file's
  metadata and in a `.credits.txt` beside it. The licence asks for that credit.

The library folder is `$ROUGHCUT_MUSIC_LIBRARY`, default `/mnt/roughcut/music-library`
(shared by every project and both boards).

    python -m roughcut.musiclib update             # (re)download the catalogue
    python -m roughcut.musiclib fetch --starter    # pre-fetch ~36 tracks across moods
    python -m roughcut.musiclib search "goofy ski trip" --seconds 90
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

PREFIX = "library/"
CATALOG_URL = "https://incompetech.com/music/royalty-free/pieces.json"
GENRE_URL = "https://incompetech.com/music/royalty-free/genre.json"
FILE_URL = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
UA = "roughcut-music-library/1.0"
TIMEOUT_S = 60
MAX_BYTES = 60 * 1024 * 1024

# Words an editor says → the catalogue's moods ("feel"). Several words can point at one feel.
MOOD_WORDS: dict[str, tuple[str, ...]] = {
    "Humorous": ("funny", "comic", "comical", "comedy", "comedic", "goofy", "silly", "quirky",
                 "playful", "cheeky", "wacky", "zany", "lighthearted", "light-hearted", "humorous",
                 "humor", "humour", "fun", "jokey", "cartoon", "cartoony", "slapstick", "mischief"),
    "Bouncy": ("bouncy", "bouncing", "peppy", "perky", "jaunty", "skippy", "cheerful", "fun",
               "comical", "goofy", "playful", "quirky", "silly"),
    "Bright": ("bright", "happy", "sunny", "cheerful", "joyful", "upbeat", "positive", "feel-good",
               "feelgood", "summer"),
    "Uplifting": ("uplifting", "inspiring", "inspirational", "hopeful", "triumphant", "emotional",
                  "heartwarming", "travel", "adventure", "sentimental", "montage"),
    "Driving": ("driving", "energetic", "energy", "hype", "pumped", "fast", "rock", "action",
                "intense", "powerful", "edm", "sport", "sports"),
    "Action": ("action", "hype", "extreme", "adrenaline", "chase", "sport", "sports", "epic"),
    "Epic": ("epic", "cinematic", "grand", "huge", "orchestral", "majestic", "trailer", "heroic"),
    "Grooving": ("groove", "grooving", "groovy", "funky", "funk", "chill", "cool", "jazzy",
                 "hip-hop", "hiphop", "lofi", "lo-fi"),
    "Relaxed": ("relaxed", "relaxing", "chill", "chilled", "mellow", "easy", "laid-back",
                "laidback", "lazy", "gentle", "soft", "lofi", "lo-fi"),
    "Calming": ("calm", "calming", "peaceful", "serene", "quiet", "ambient", "soothing", "dreamy"),
    "Mysterious": ("mysterious", "mystery", "sneaky", "spy", "curious", "tense"),
    "Suspenseful": ("suspense", "suspenseful", "tense", "tension", "dramatic", "thriller"),
    "Somber": ("sad", "somber", "sombre", "melancholy", "melancholic", "reflective", "nostalgic",
               "moving", "tearjerker", "bittersweet"),
    "Dark": ("dark", "ominous", "brooding", "gloomy"),
    "Eerie": ("eerie", "spooky", "creepy", "haunted", "halloween"),
    "Unnerving": ("unnerving", "unsettling", "creepy", "disturbing"),
    "Intense": ("intense", "intensity", "aggressive", "heavy", "metal"),
    "Aggressive": ("aggressive", "angry", "metal", "hard"),
    "Mystical": ("mystical", "magical", "fantasy", "fairy", "enchanted", "whimsical"),
}
# feels a travel film never wants unless the words ask for them
UNASKED_PENALTY = {"Dark", "Eerie", "Unnerving", "Aggressive", "Somber", "Suspenseful", "Mysterious"}
# what "add music that fits" means for a trip film when the words name no mood
DEFAULT_FEELS = ("Bright", "Uplifting", "Bouncy", "Grooving", "Driving")
# genre words (incompetech genre names, lower case) an editor might say
GENRE_WORDS = {"jazz": "jazz", "rock": "rock", "funk": "funk", "funky": "funk", "polka": "polka",
               "reggae": "reggae", "ska": "ska", "latin": "latin", "disco": "disco", "blues": "blues",
               "classical": "classical", "electronic": "electronica", "electronica": "electronica",
               "edm": "electronica", "pop": "pop", "silent film": "silent film score",
               "orchestral": "soundtrack", "cinematic": "soundtrack", "holiday": "holiday",
               "christmas": "holiday", "world": "world", "hip-hop": "urban", "hip hop": "urban",
               "dixieland": "jazz", "ragtime": "jazz"}

# The starter set: a spread across what trip films ask for, fetched by `fetch --starter`.
STARTER_FEELS = (("Humorous", 10), ("Bouncy", 6), ("Bright", 5), ("Uplifting", 5),
                 ("Driving", 4), ("Grooving", 3), ("Relaxed", 3), ("Epic", 2))

_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()
_CACHE: dict = {}


class LibraryError(RuntimeError):
    """In words an editor can act on."""


def root() -> Path:
    return Path(os.environ.get("ROUGHCUT_MUSIC_LIBRARY", "/mnt/roughcut/music-library")).expanduser()


def files_dir() -> Path:
    return root() / "files"


def is_library(asset: str) -> bool:
    return str(asset or "").startswith(PREFIX)


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")[:80] or "track"


def _seconds(hms: str) -> float | None:
    try:
        parts = [float(x) for x in str(hms).split(":")]
    except ValueError:
        return None
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s or None


def _get(url: str, limit: int = MAX_BYTES) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise LibraryError(f"{url} is larger than {limit // 2**20} MB")
    return data


def update(base: Path | None = None) -> int:
    """Download the catalogue (and the genre key) into the library; the track count."""
    base = base or root()
    base.mkdir(parents=True, exist_ok=True)
    pieces = json.loads(_get(CATALOG_URL, 20 * 2**20))
    try:
        genres = {str(g["id"]): g["genre"] for g in json.loads(_get(GENRE_URL, 2**20))}
    except Exception:  # noqa: BLE001 — the genre key is a nicety
        genres = {}
    if not isinstance(pieces, list) or len(pieces) < 100:
        raise LibraryError("the catalogue came back without its tracks")
    tmp = base / ".catalog.json.part"
    tmp.write_text(json.dumps({"fetched": time.time(), "source": CATALOG_URL, "genres": genres,
                               "pieces": pieces}), encoding="utf-8")
    tmp.replace(base / "catalog.json")
    _CACHE.clear()
    return len(pieces)


def _normalise(p: dict, genres: dict) -> dict | None:
    title, fname = (p.get("title") or "").strip(), (p.get("filename") or "").strip()
    if not title or not fname.lower().endswith(".mp3") or "/" in fname or "\\" in fname:
        return None
    feel = [f.strip() for f in (p.get("feel") or "").split(",") if f.strip()]
    try:
        bpm = int(float(p.get("bpm") or 0)) or None
    except ValueError:
        bpm = None
    return {"id": slug(title), "title": title, "file": fname, "seconds": _seconds(p.get("length")),
            "bpm": bpm, "feel": feel, "genre": genres.get(str(p.get("genre")), ""),
            "instruments": (p.get("instruments") or "").strip(),
            "description": re.sub(r"\s+", " ", (p.get("description") or "")).strip()}


def load(base: Path | None = None) -> list[dict]:
    """Every track in the catalogue, normalised; [] when there is no catalogue yet.
    Cached by the file's mtime."""
    base = base or root()
    cat = base / "catalog.json"
    try:
        mt = cat.stat().st_mtime
    except OSError:
        return []
    key = (str(cat), mt)
    with _LOCK:
        if _CACHE.get("key") == key:
            return _CACHE["tracks"]
        try:
            raw = json.loads(cat.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        genres = raw.get("genres") or {}
        out, seen = [], set()
        for p in raw.get("pieces") or []:
            t = _normalise(p, genres) if isinstance(p, dict) else None
            if t and t["id"] not in seen:
                seen.add(t["id"])
                out.append(t)
        _CACHE.update(key=key, tracks=out, by_id={t["id"]: t for t in out})
        return out


def get(track_id: str, base: Path | None = None) -> dict | None:
    tid = str(track_id or "")
    if tid.startswith(PREFIX):
        tid = tid[len(PREFIX):]
    load(base)
    return (_CACHE.get("by_id") or {}).get(tid) or (_CACHE.get("by_id") or {}).get(slug(tid))


def local_path(track: dict, base: Path | None = None) -> Path:
    return (base or root()) / "files" / Path(track["file"]).name


def have(track: dict, base: Path | None = None) -> bool:
    return local_path(track, base).is_file()


def fetch(track: dict, base: Path | None = None) -> Path:
    """The track's file, downloaded once and kept. Raises LibraryError in plain words."""
    out = local_path(track, base)
    if out.is_file():
        return out
    with _FETCH_LOCK:
        if out.is_file():
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        url = FILE_URL + urllib.parse.quote(track["file"])
        try:
            data = _get(url)
        except LibraryError:
            raise
        except Exception as exc:  # noqa: BLE001 — offline, a 404, a timeout: one sentence
            raise LibraryError(f"could not download {track['title']} from incompetech.com ({exc}); "
                               f"is this computer online?")
        if not (data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)):
            raise LibraryError(f"{track['title']}: incompetech.com did not send an mp3")
        part = out.with_name(f".{out.name}.part")
        part.write_bytes(data)
        part.replace(out)
    return out


def path(asset: str, base: Path | None = None) -> Path:
    """The file a `library/<id>` asset names, fetched if it is not here yet."""
    t = get(asset, base)
    if t is None:
        raise FileNotFoundError(f"music not found in the library: {asset}")
    return fetch(t, base)


def measured(track: dict, measure, base: Path | None = None) -> dict:
    """{"duration_s", "lufs"} for a track that is here, measured once and kept on disk
    (`<library>/measured.json`): forty tracks measured on every board start was ~45 s
    before the first ask could run. `measure(path)` returns that dict."""
    base = base or root()
    p = local_path(track, base)
    st = p.stat()
    key = f"{p.name}|{st.st_size}|{int(st.st_mtime)}"
    db_path = base / "measured.json"
    with _LOCK:
        try:
            db = json.loads(db_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            db = {}
        if key in db:
            return db[key]
    got = measure(p)
    with _LOCK:
        try:
            db = json.loads(db_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            db = {}
        db[key] = got
        try:
            tmp = db_path.with_name(".measured.json.part")
            tmp.write_text(json.dumps(db), encoding="utf-8")
            tmp.replace(db_path)
        except OSError:
            pass
    return got


def credit(track: dict) -> str:
    return (f"Music: “{track['title']}” by Kevin MacLeod (incompetech.com), "
            f"licensed under Creative Commons: By Attribution 4.0 ({LICENSE_URL})")


def credit_for(asset: str | None, base: Path | None = None) -> str | None:
    if not is_library(asset or ""):
        return None
    t = get(asset, base)
    return credit(t) if t else None


def asked_feels(words: str) -> tuple[dict[str, float], set[str]]:
    """Moods and genres the words name: ({feel: weight}, {genre})."""
    low = " " + re.sub(r"[^a-z0-9 -]+", " ", str(words or "").lower()) + " "
    feels: dict[str, float] = {}
    for feel, keys in MOOD_WORDS.items():
        n = sum(1 for k in keys if f" {k} " in low)
        if n:
            feels[feel] = feels.get(feel, 0.0) + 1.0 + 0.25 * (n - 1)
    genres = {g for w, g in GENRE_WORDS.items() if f" {w} " in low}
    return feels, genres


def score(track: dict, feels: dict[str, float], genres: set[str], film_s: float | None,
          words: str = "", base: Path | None = None) -> float:
    want = feels or {f: 0.6 for f in DEFAULT_FEELS}
    tf = track["feel"]
    s = 0.0
    for f in tf:                            # the catalogue lists feels A to Z: no order to read
        if f in want:
            s += want[f]
        elif f in UNASKED_PENALTY:
            s -= 0.6
    if genres and (track["genre"] or "").lower() in genres:
        s += 1.2
    low = str(words or "").lower()
    if low and track["description"]:
        d = track["description"].lower()
        s += 0.15 * sum(1 for w in set(re.findall(r"[a-z]{5,}", low)) if w in d)
    secs = track["seconds"] or 0
    if film_s:                               # a bed shorter than the film runs out before the end
        if secs < film_s:
            s -= 1.5 * min(1.0, (film_s - secs) / max(film_s, 1.0)) + 0.5
        elif secs > film_s * 4 + 120:
            s -= 0.2                         # very long: most of it is never heard; a mild nudge
    if (track["genre"] or "").lower() == "stings":
        s -= 2.0                              # a 5-second sting is not a bed
    if have(track, base):
        s += 0.1
    return s


def search(words: str, film_s: float | None = None, n: int = 12, base: Path | None = None) -> list[dict]:
    """The `n` tracks that best fit the words and the film's length, best first."""
    tracks = load(base)
    if not tracks:
        return []
    feels, genres = asked_feels(words)
    ranked = sorted(tracks, key=lambda t: (-score(t, feels, genres, film_s, words, base), t["title"]))
    return ranked[:n]


def row(t: dict, base: Path | None = None) -> str:
    secs = t["seconds"] or 0
    bits = [f"{PREFIX}{t['id']}", f"“{t['title']}”", ", ".join(t["feel"]) or "no mood listed"]
    if t["bpm"]:
        bits.append(f"{t['bpm']} bpm")
    bits.append(f"{int(secs // 60)}:{int(secs % 60):02d}")
    if t["genre"]:
        bits.append(t["genre"])
    if t["instruments"]:
        bits.append(t["instruments"][:60])
    line = " · ".join(bits)
    if t["description"]:
        line += f" — {t['description'][:140]}" + ("…" if len(t["description"]) > 140 else "")
    return line


def starter(base: Path | None = None) -> list[dict]:
    tracks = load(base)
    out, seen = [], set()
    for feel, k in STARTER_FEELS:
        pool = [t for t in tracks if feel in t["feel"] and not (set(t["feel"]) & UNASKED_PENALTY) and t["id"] not in seen
                and (t["seconds"] or 0) >= 100 and (t["genre"] or "").lower() != "stings"]
        pool.sort(key=lambda t: t["title"])   # stable, deterministic
        step = max(1, len(pool) // max(k, 1))
        for t in pool[::step][:k]:
            seen.add(t["id"])
            out.append(t)
    return out


def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m roughcut.musiclib")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update")
    f = sub.add_parser("fetch")
    f.add_argument("ids", nargs="*")
    f.add_argument("--starter", action="store_true")
    s = sub.add_parser("search")
    s.add_argument("words")
    s.add_argument("--seconds", type=float)
    s.add_argument("-n", type=int, default=12)
    a = ap.parse_args(argv)
    if a.cmd == "update":
        print(f"{update()} tracks in {root() / 'catalog.json'}")
        return 0
    if a.cmd == "fetch":
        if not load():
            print(f"{update()} tracks in the catalogue")
        want = starter() if a.starter else [t for t in (get(i) for i in a.ids) if t]
        for t in want:
            try:
                p = fetch(t)
                print(f"ok  {t['id']:<40} {', '.join(t['feel'])}  {p.stat().st_size // 1024} KB")
            except LibraryError as exc:
                print(f"!!  {t['id']}: {exc}")
            time.sleep(0.5)                  # be polite to a one-person site
        return 0
    for t in search(a.words, a.seconds, a.n):
        print(("[here] " if have(t) else "       ") + row(t))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
