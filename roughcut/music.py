"""A project's own music (wave 24, lane DELIVER; FINISH-02, WORDS-16).

The repo's `assets/music` is a shelf for the developer; an editor in the browser has no way
to put a file there. A track brought in from the board lands under the project's work dir,
`<work>/music/<bin>/`, and is named `project/<file>` in the cut's `effects_music.asset`
(the repo's tracks keep the names they had: `music/<file>`). `resolve()` turns either into
a path; the render is handed the absolute path of the file, so what it reads is the file
that was chosen when the export started.

The board has no login and listens on localhost, so everything that writes here is
constrained to that one folder: the name is cut to a single sanitised component and the
suffix is on a short list, whatever the request says.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

PREFIX = "project/"
ALLOWED = (".mp3", ".wav", ".m4a", ".flac")
MAX_BYTES = 200 * 1024 * 1024          # 200 MB: a 10 minute 24-bit WAV is ~170 MB
MAX_LABEL = "200 MB"
NAME_MAX = 80


class MusicError(ValueError):
    """The reason, in plain words, a file was not taken."""


def folder(work: Path, bin_name: str) -> Path:
    return work / "music" / bin_name


def is_project(asset: str) -> bool:
    return str(asset).startswith(PREFIX)


def clean_name(name: str) -> str:
    """One path component, safe characters only, a suffix from `ALLOWED`; raises
    `MusicError` (in words an editor can act on) for anything else."""
    raw = Path(str(name or "").replace("\\", "/")).name.strip()
    if not raw or raw in (".", ".."):
        raise MusicError("the file has no name")
    stem, dot, ext = raw.rpartition(".")
    ext = ("." + ext.lower()) if dot else ""
    if ext not in ALLOWED:
        have = ext or "no extension"
        raise MusicError(f"that is not a music file the board takes ({have}); "
                         f"use mp3, wav, m4a or flac")
    stem = re.sub(r"[^A-Za-z0-9 ._()-]+", "-", stem if dot else raw)
    stem = re.sub(r"\s+", " ", stem).strip(" .-")[:NAME_MAX].strip(" .-")
    return (stem or "music") + ext


def free_name(dest: Path, name: str) -> str:
    """`name`, or `name-2.ext`, `-3` … : never over a track that is already there."""
    p = Path(name)
    out, n = name, 2
    while (dest / out).exists():
        out = f"{p.stem}-{n}{p.suffix}"
        n += 1
    return out


def resolve(asset: str, work: Path, bin_name: str, assets_root: Path | None) -> Path:
    """The file an `effects_music.asset` names. A project track is found only inside the
    project's music folder (a `..` in the name cannot leave it); anything else is the
    effects module's own rule (the repo's assets, or an absolute path)."""
    from roughcut import effects
    if is_project(asset):
        name = Path(asset[len(PREFIX):]).name
        p = folder(work, bin_name) / name
        if not name or not p.is_file():
            raise FileNotFoundError(f"music not found: {asset}")
        return p
    return effects.resolve_asset(asset, assets_root)


def sniff_audio(path: Path) -> bool:
    """True when the file's own bytes look like what its suffix says (a renamed video or
    text file is refused rather than handed to ffmpeg at render time)."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    ext = path.suffix.lower()
    if ext == ".wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if ext == ".flac":
        return head[:4] == b"fLaC"
    if ext == ".m4a":
        return head[4:8] == b"ftyp"
    if ext == ".mp3":
        return head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
    return False


def take_file(src: Path, work: Path, bin_name: str, name: str | None = None) -> Path:
    """Copy a file already on this machine into the project's music folder."""
    try:
        src = src.expanduser().resolve()
    except OSError as exc:
        raise MusicError(f"cannot read that path: {exc}")
    if src.is_dir():
        raise MusicError("that path is a folder; give the path of one music file")
    if not src.is_file():
        raise MusicError("no file at that path on this computer")
    safe = clean_name(name or src.name)
    size = src.stat().st_size
    if size > MAX_BYTES:
        raise MusicError(f"that file is {size / 2**20:.0f} MB; the limit is {MAX_LABEL}")
    if not sniff_audio(src):
        raise MusicError("that does not look like a music file (its contents do not match "
                         "its extension)")
    dest = folder(work, bin_name)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / free_name(dest, safe)
    part = dest / f".{out.name}.part"
    try:
        shutil.copyfile(src, part)
        part.replace(out)
    finally:
        part.unlink(missing_ok=True)
    return out
