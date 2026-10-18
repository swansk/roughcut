"""The project's footage from anywhere (REDESIGN-GOAL dimension 13, Karl's K3 b).

Karl, 2026-10-16: *"there is no clear way to add new footage."* A bin was one folder,
named on the command line; the only way to add a clip was to copy it into that folder
from a shell. Premiere's model is a project that *references* media wherever it lives:
File › Import adds files or a folder to the project without moving them. This module is
that model for a bin:

  * **the folder** — `--footage`, listed as it is on disk (a clip's name is its file name);
  * **imports** — files (or every video under a folder) elsewhere on the machine that runs
    the board, referenced in place, never copied;
  * **uploads** — files sent from the viewer's own computer (the board runs on foxtrot and
    is opened from a laptop over an SSH tunnel), streamed to disk under the bin's `--work`
    media dir, never under the footage folder.

The list of imports and uploads is `work/media/<bin>/sources.json`, per bin like every
other derived dir, so it survives a restart and a re-point. A clip's **name is its
identity** everywhere (sidecars, proxies, posters and the cut are keyed by it and by its
stem), so an imported file gets a name once, at import, and keeps it: its own file name
made URL-safe (`[A-Za-z0-9._-]`, what the media routes accept), or, when that stem is
taken (a second `C0001.MP4` from another card), the stem plus its folder's name
(`C0001-dayB.MP4`), then a counter. Stems compare case-insensitively, so two names that
would share a proxy on a case-insensitive disk never coexist.

Pure helpers plus one small state file; the server owns the wiring (`server.clip_path`,
`server.tool_footage`). Nothing here runs ffprobe: browsing a folder of 4K masters lists
names, sizes and dates only.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

# Kept in step with app/server.py and research/tools/audio_analyze.py.
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".webm"}

SOURCES_FILE = "sources.json"
VERSION = 1

# An import walks a folder this deep and stops at this many files: a mistaken click on
# `/mnt` must not freeze the board listing a disk.
SCAN_DEPTH = 6
SCAN_LIMIT = 2000
BROWSE_LIMIT = 4000


def media_dir(work: Path, bin_name: str) -> Path:
    return Path(work) / "media" / bin_name


def uploads_dir(work: Path, bin_name: str) -> Path:
    return media_dir(work, bin_name) / "uploads"


def view_dir(work: Path, bin_name: str) -> Path:
    """The folder the tools read when the bin has more than its folder: one symlink per
    clip, named as the clip (`sync_view`)."""
    return media_dir(work, bin_name) / "view"


def is_video(p: Path | str) -> bool:
    return Path(p).suffix.lower() in VIDEO_SUFFIXES


def safe_name(name: str) -> str:
    """A file name the board can serve: `[A-Za-z0-9._-]`, the extension kept.

    `My Clip (1).MOV` → `My_Clip_1.MOV`. Empty or all-symbol stems become `clip`."""
    p = Path(str(name or "").replace("\\", "/")).name
    ext = Path(p).suffix
    stem = p[: -len(ext)] if ext else p
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    stem = re.sub(r"_+", "_", stem)[:80] or "clip"
    if stem.startswith("gen_"):          # the generated slides' prefix (edits.is_generated)
        stem = f"clip_{stem}"
    ext = re.sub(r"[^A-Za-z0-9]+", "", ext)[:8]
    return f"{stem}.{ext}" if ext else stem


def _hint(text: str) -> str:
    h = re.sub(r"[^A-Za-z0-9_-]+", "_", str(text or "")).strip("._-")
    return re.sub(r"_+", "_", h)[:40]


def unique_name(wanted: str, taken: set[str], hint: str = "") -> str:
    """`wanted` made safe, unless its stem (lowercased) is in `taken`: then
    `<stem>-<hint><ext>`, then `<stem>-<hint>-2<ext>`, ... The caller adds the result's
    stem to `taken`."""
    name = safe_name(wanted)
    ext = Path(name).suffix
    stem = name[: -len(ext)] if ext else name
    if stem.lower() not in taken:
        return name
    h = _hint(hint)
    base = f"{stem}-{h}" if h and h.lower() != stem.lower() else f"{stem}-2"
    if base.lower() not in taken:
        return f"{base}{ext}"
    n = 2
    while f"{base}-{n}".lower() in taken:
        n += 1
    return f"{base}-{n}{ext}"


def stem_key(name: str) -> str:
    return Path(name).stem.lower()


class Sources:
    """The bin's imports and uploads, persisted in `sources.json`.

    `entries` keep their order (import order); each is
    `{clip, path, kind: "import"|"upload", added, from}` where `from` is the name the
    file had where it came from. Thread-safe: imports, uploads and the request threads
    that list the clips all reach it."""

    def __init__(self, work: Path, bin_name: str):
        self.dir = media_dir(work, bin_name)
        self.path = self.dir / SOURCES_FILE
        self.bin = bin_name
        self.lock = threading.RLock()
        self.entries: list[dict] = []
        self.last_browse: str | None = None
        self.load()

    # ---------------------------------------------------------------- disk

    def load(self) -> None:
        with self.lock:
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                d = {}
            rows = d.get("entries") if isinstance(d, dict) else None
            self.entries = [r for r in (rows or []) if isinstance(r, dict)
                            and isinstance(r.get("clip"), str) and isinstance(r.get("path"), str)]
            self.last_browse = d.get("last_browse") if isinstance(d, dict) else None

    def save(self) -> None:
        with self.lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{SOURCES_FILE}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"version": VERSION, "bin": self.bin,
                                       "entries": self.entries,
                                       "last_browse": self.last_browse}, indent=1),
                           encoding="utf-8")
            tmp.replace(self.path)

    # ---------------------------------------------------------------- reading

    def __bool__(self) -> bool:
        return bool(self.entries)

    def path_of(self, clip: str) -> Path | None:
        with self.lock:
            for r in self.entries:
                if r["clip"] == clip:
                    return Path(r["path"])
        return None

    def entry(self, clip: str) -> dict | None:
        with self.lock:
            return next((dict(r) for r in self.entries if r["clip"] == clip), None)

    def present(self) -> list[tuple[str, Path]]:
        """(clip, path) for every entry whose file is there now: an unplugged drive's
        clips are not listed (the folder's own clips follow the same rule: files are
        the truth), and come back with the drive."""
        with self.lock:
            rows = [(r["clip"], Path(r["path"])) for r in self.entries]
        return [(c, p) for c, p in rows if p.is_file()]

    def real_paths(self) -> set[str]:
        with self.lock:
            return {os.path.realpath(r["path"]) for r in self.entries}

    # ---------------------------------------------------------------- writing

    def add(self, files: list[tuple[Path, str]], kind: str, folder_names: list[str],
            *, origin: dict[str, str] | None = None) -> tuple[list[str], list[dict]]:
        """Add `files` — (path, hint) pairs — as `kind`. Returns (clip names added,
        skipped rows {path, why}). A file already in the project (the folder's own, or
        imported before, by real path) is skipped, not added twice. Names are unique
        against the folder's clips and every entry (`unique_name`)."""
        added: list[str] = []
        skipped: list[dict] = []
        with self.lock:
            taken = {stem_key(n) for n in folder_names} | {stem_key(r["clip"])
                                                         for r in self.entries}
            have = self.real_paths()
            for path, hint in files:
                real = os.path.realpath(path)
                if real in have:
                    skipped.append({"path": str(path), "why": "already in the project"})
                    continue
                name = unique_name((origin or {}).get(str(path)) or Path(path).name,
                                   taken, hint)
                taken.add(stem_key(name))
                have.add(real)
                self.entries.append({"clip": name, "path": str(path), "kind": kind,
                                     "added": round(time.time(), 3),
                                     "from": (origin or {}).get(str(path)) or Path(path).name})
                added.append(name)
            if added:
                self.save()
        return added, skipped

    def reserve(self, wanted: str, hint: str, folder_names: list[str],
                pending: set[str]) -> str:
        """A unique name for an upload that has not landed yet (its batch holds it in
        `pending` so two files of one batch never get the same one)."""
        with self.lock:
            taken = ({stem_key(n) for n in folder_names}
                     | {stem_key(r["clip"]) for r in self.entries} | pending)
            return unique_name(wanted, taken, hint)

    def add_upload(self, clip: str, path: Path, came_as: str) -> None:
        with self.lock:
            self.entries = [r for r in self.entries if r["clip"] != clip]
            self.entries.append({"clip": clip, "path": str(path), "kind": "upload",
                                 "added": round(time.time(), 3), "from": came_as})
            self.save()

    def find_upload(self, came_as: str, size: int) -> str | None:
        """An upload of the same file (its original name and size) already landed."""
        with self.lock:
            for r in self.entries:
                if r.get("kind") != "upload" or r.get("from") != came_as:
                    continue
                try:
                    if Path(r["path"]).stat().st_size == int(size):
                        return r["clip"]
                except OSError:
                    continue
        return None

    def remove(self, clips: list[str]) -> list[dict]:
        """Take entries out of the project (Premiere's Clear). The referenced files are
        never touched; the caller decides about an upload's own copy."""
        with self.lock:
            gone = [dict(r) for r in self.entries if r["clip"] in set(clips)]
            if gone:
                self.entries = [r for r in self.entries if r["clip"] not in set(clips)]
                self.save()
            return gone

    def remember_browse(self, path: str) -> None:
        with self.lock:
            if self.last_browse != path:
                self.last_browse = path
                self.save()


# -------------------------------------------------------------------- the file system


def scan(paths: list[Path], *, depth: int = SCAN_DEPTH,
         limit: int = SCAN_LIMIT) -> tuple[list[tuple[Path, str]], list[dict]]:
    """Expand what an import names into video files: a file is itself; a folder is every
    video under it (hidden entries skipped, `depth` levels, `limit` files in all, sorted
    as a file manager sorts). Each file comes with its collision hint — its parent
    folder's name. Returns (files, skipped rows {path, why})."""
    out: list[tuple[Path, str]] = []
    skipped: list[dict] = []
    seen: set[str] = set()

    def take(p: Path) -> bool:
        if len(out) >= limit:
            return False
        real = os.path.realpath(p)
        if real not in seen:
            seen.add(real)
            out.append((p, p.parent.name))
        return True

    def walk(d: Path, level: int) -> bool:
        try:
            kids = sorted(d.iterdir(), key=lambda q: (q.name.lower(), q.name))
        except OSError as exc:
            skipped.append({"path": str(d), "why": f"cannot read: {exc.strerror or exc}"})
            return True
        for q in kids:
            if q.name.startswith("."):
                continue
            try:
                if q.is_file() and is_video(q):
                    if not take(q):
                        return False
            except OSError:
                continue
        if level >= depth:
            return True
        for q in kids:
            if q.name.startswith("."):
                continue
            try:
                if q.is_dir() and not walk(q, level + 1):
                    return False
            except OSError:
                continue
        return True

    for raw in paths:
        p = Path(raw).expanduser()
        try:
            if p.is_dir():
                before = len(out)
                if not walk(p, 0):
                    skipped.append({"path": str(p), "why": f"stopped at {limit} files"})
                    break
                if len(out) == before:
                    skipped.append({"path": str(p), "why": "no video in this folder"})
            elif p.is_file():
                if is_video(p):
                    if not take(p):
                        skipped.append({"path": str(p), "why": f"stopped at {limit} files"})
                        break
                else:
                    skipped.append({"path": str(p), "why": "not a video file"})
            else:
                skipped.append({"path": str(p), "why": "no such file or folder"})
        except OSError as exc:
            skipped.append({"path": str(p), "why": f"cannot read: {exc.strerror or exc}"})
    return out, skipped


def browse(path: Path, *, in_project: set[str] | None = None,
           limit: int = BROWSE_LIMIT) -> dict:
    """One folder as the Media Browser lists it: its sub-folders and its video files
    (name, size, modified), hidden entries and other files left out — no ffprobe.
    `in_project` holds real paths already in the bin (marked, not hidden). Raises
    FileNotFoundError / NotADirectoryError / PermissionError for the route to answer."""
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(str(p))
    if not p.is_dir():
        raise NotADirectoryError(str(p))
    p = Path(os.path.abspath(p))
    rows: list[dict] = []
    others = 0
    with os.scandir(p) as it:
        for e in it:
            if e.name.startswith("."):
                continue
            try:
                if e.is_dir():
                    rows.append({"name": e.name, "path": os.path.join(p, e.name),
                                 "kind": "dir", "mtime": e.stat().st_mtime})
                elif e.is_file() and is_video(e.name):
                    st = e.stat()
                    full = os.path.join(p, e.name)
                    rows.append({"name": e.name, "path": full, "kind": "video",
                                 "size": st.st_size, "mtime": st.st_mtime,
                                 "in_project": os.path.realpath(full) in (in_project or set())})
                else:
                    others += 1
            except OSError:
                continue
            if len(rows) >= limit:
                break
    rows.sort(key=lambda r: (r["kind"] != "dir", r["name"].lower(), r["name"]))
    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "name": p.name or str(p), "parent": parent, "entries": rows,
            "videos": sum(1 for r in rows if r["kind"] == "video"),
            "folders": sum(1 for r in rows if r["kind"] == "dir"),
            "others": others, "truncated": len(rows) >= limit}


def roots(footage: Path, home: Path | None = None) -> list[dict]:
    """The Media Browser's places: the bin's own folder, the folder that holds it (a
    trip's bins sit side by side), home, and the mounts (/mnt, /media/<user>)."""
    home = Path(home or Path.home())
    out: list[dict] = []
    seen: set[str] = set()

    def add(name: str, p: Path, kind: str) -> None:
        try:
            ok = p.is_dir()
        except OSError:
            ok = False
        key = os.path.realpath(p)
        if ok and key not in seen:
            seen.add(key)
            out.append({"name": name, "path": str(p), "kind": kind})

    footage = Path(footage)
    add(footage.name or str(footage), footage, "bin")
    if footage.parent != footage:
        add(footage.parent.name or str(footage.parent), footage.parent, "folder")
    add("Home", home, "home")
    # the mounts: /mnt, and /media/<user> (where a desktop mounts a card) else /media
    add("/mnt", Path("/mnt"), "drive")
    user_media = Path("/media") / home.name
    add(str(user_media), user_media, "drive")
    if not any(r["path"] == str(user_media) for r in out):
        add("/media", Path("/media"), "drive")
    return out


def sync_view(view: Path, mapping: dict[str, Path]) -> Path:
    """Make `view` hold exactly one symlink per clip, named as the clip, pointing at its
    file: the folder the tools (`audio_analyze.py`, `visual_pass.py`, `telemetry.py`,
    `assemble.py`) are given, since each of them takes one folder and joins clip names
    onto it. Never copies media. Returns `view`."""
    view.mkdir(parents=True, exist_ok=True)
    want = {name: os.path.abspath(p) for name, p in mapping.items()}
    for q in view.iterdir():
        target = want.get(q.name)
        if target is None or not q.is_symlink() or os.readlink(q) != target:
            try:
                q.unlink()
            except OSError:
                pass
    for name, target in want.items():
        q = view / name
        if not q.is_symlink():
            try:
                q.symlink_to(target)
            except FileExistsError:
                pass
    return view
