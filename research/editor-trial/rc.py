"""kit/rc.py: how a simulated editor sees and touches the board.

    from rc import *
    with board() as page:                 # your one open browser tab, exactly as you left it
        page.mouse.click(40, 12)          # real pointer and key events only
        shot(page, "file-menu")           # then Read the PNG it prints: that is your eyes

Nothing here calls the app's own functions or its write APIs. `controls()` and `texts()` read what
is on screen (to get coordinates and exact wording); they change nothing.
"""
import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(os.environ.get("RC_ROOT", "/mnt/roughcut/space/roughcut-loop/copper"))
NAME = os.environ["RC_EDITOR"]
ED = ROOT / "editors" / NAME
OUT = ED / "out"
SHOTS = OUT / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)
CONF = json.loads((ED / "editor.json").read_text())
URL = CONF["url"]
DIALOG = "accept"          # what to answer a native confirm / prompt with: "accept" or "dismiss"
_T0 = time.time()


def _stamp() -> str:
    return time.strftime("%H:%M:%S")


@contextmanager
def board():
    """Attach to this editor's long-lived browser tab. Leaving the block detaches; the tab stays."""
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(CONF["cdp"])
        ctx = browser.contexts[0]
        pages = [p for p in ctx.pages if p.url.startswith(URL)] or ctx.pages
        page = pages[0] if pages else ctx.new_page()
        page.set_default_timeout(15000)

        def on_dialog(d):
            print(f"DIALOG {d.type}: {d.message!r} -> {DIALOG}", flush=True)
            note(f"native dialog ({d.type}): {d.message!r}; answered {DIALOG}")
            d.accept() if DIALOG == "accept" else d.dismiss()
        page.on("dialog", on_dialog)
        page.on("pageerror", lambda e: (print("PAGE ERROR:", str(e)[:300], flush=True),
                                        note(f"page error: {str(e)[:300]}")))
        if not page.url.startswith(URL) or "/api/" in page.url:      # blank, or the warm-up endpoint
            page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        yield page
    finally:
        pw.stop()


def _next(label: str) -> Path:
    n = len(list(SHOTS.glob("[0-9][0-9][0-9]-*.png"))) + 1      # not watch()'s temporary looks
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:60]
    return SHOTS / f"{n:03d}-{safe}.png"


def shot(page, label: str, clip: dict | None = None) -> str:
    """A screenshot of the tab (or of `clip` = {x, y, width, height}). Prints the path: Read it."""
    p = _next(label)
    page.screenshot(path=str(p), **({"clip": clip} if clip else {}))
    print("SHOT", p, flush=True)
    return str(p)


def watch(page, seconds: float, frames: int = 8, label: str = "watch", clip: dict | None = None) -> str:
    """Look at the screen `frames` times over `seconds` (while something plays or a job runs) and
    lay the looks side by side in one image, oldest first. Prints the path: Read it."""
    from PIL import Image
    tmp = []
    gap = seconds / max(1, frames - 1)
    t0 = time.time()
    for i in range(frames):
        wait = t0 + i * gap - time.time()
        if wait > 0:
            page.wait_for_timeout(wait * 1000)
        q = SHOTS / f".w{i}.png"
        page.screenshot(path=str(q), **({"clip": clip} if clip else {}))
        tmp.append(q)
    ims = [Image.open(q) for q in tmp]
    w = 640
    ims = [im.resize((w, round(im.height * w / im.width))) for im in ims]
    cols = 4 if frames > 4 else frames
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * ims[0].height), "black")
    for i, im in enumerate(ims):
        sheet.paste(im, ((i % cols) * w, (i // cols) * ims[0].height))
    p = _next(label)
    sheet.save(p)
    for q in tmp:
        q.unlink(missing_ok=True)
    print("SHOT", p, f"({frames} looks over {seconds:.0f} s)", flush=True)
    return str(p)


def controls(page, within: str | None = None, limit: int = 160) -> list[dict]:
    """What a person could click or type into right now: visible buttons, menu items, inputs,
    links, with their words, tooltip and centre point. Reads the screen; changes nothing."""
    js = """([sel, limit]) => {
      const root = sel ? document.querySelector(sel) : document; if (!root) return [];
      const q = 'button, [role=button], [role=menuitem], [role=tab], [role=option], a[href], input, select, textarea, summary, [contenteditable=true], [tabindex]:not([tabindex="-1"])';
      const out = [];
      for (const e of root.querySelectorAll(q)) {
        const r = e.getBoundingClientRect(); if (r.width < 4 || r.height < 4) continue;
        const cs = getComputedStyle(e); if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) continue;
        if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) continue;
        const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        const covered = !(top && (e.contains(top) || top.contains(e)));
        out.push({tag: e.tagName.toLowerCase(), role: e.getAttribute('role') || '', type: e.type || '',
          text: (e.innerText || e.value || e.placeholder || '').trim().replace(/\\s+/g, ' ').slice(0, 70),
          tip: (e.title || e.getAttribute('aria-label') || '').slice(0, 90),
          x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
          w: Math.round(r.width), h: Math.round(r.height), disabled: !!e.disabled || e.getAttribute('aria-disabled') === 'true', covered});
        if (out.length >= limit) break;
      }
      return out; }"""
    rows = page.evaluate(js, [within, limit])
    for r in rows:
        flags = (" DISABLED" if r["disabled"] else "") + (" (covered)" if r["covered"] else "")
        print(f"  ({r['x']:4d},{r['y']:4d}) {r['w']:4d}x{r['h']:<3d} {r['tag']}{'/' + r['role'] if r['role'] else ''}"
              f" | {r['text']!r}" + (f" | tip: {r['tip']!r}" if r["tip"] else "") + flags)
    return rows


def texts(page, selector: str = "body", limit: int = 4000) -> str:
    """The words on screen inside `selector`, as a person would read them."""
    t = page.evaluate("(s) => { const e = document.querySelector(s); return e ? e.innerText : null; }", selector)
    t = "" if t is None else " ".join(t.split())
    print(t[:limit], flush=True)
    return t


def film_sheet(video_path: str, label: str = "export", n: int = 12) -> str:
    """Watch a rendered file the way a person skims it: `n` frames evenly across it in one image,
    plus its length, picture size, frame rate and loudness. Prints the path: Read it."""
    pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                         "format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate,channels",
                         "-of", "json", video_path], capture_output=True, text=True)
    info = json.loads(pr.stdout or "{}")
    dur = float((info.get("format") or {}).get("duration") or 0)
    print("FILE", video_path, json.dumps(info)[:600], flush=True)
    p = _next(label)
    if dur > 0:
        cols = 4
        rows = (n + cols - 1) // cols
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video_path, "-vf",
                        f"fps={n / dur:.6f},scale=640:-1,tile={cols}x{rows}", "-frames:v", "1", str(p)], check=False)
        vd = subprocess.run(["ffmpeg", "-hide_banner", "-i", video_path, "-af", "volumedetect", "-vn", "-f", "null", "-"],
                            capture_output=True, text=True)
        print("SOUND", " ".join(l.split("] ")[-1] for l in vd.stderr.splitlines() if "_volume" in l), flush=True)
        print("SHOT", p, flush=True)
    return str(p)


def note(text: str) -> None:
    """One line in your running journal (out/journal.md): what you are doing and thinking."""
    with open(OUT / "journal.md", "a", encoding="utf-8") as f:
        f.write(f"- {_stamp()} {text}\n")


def finding(title: str, wanted: str, did: str, happened: str, severity: int, kind: str, where: str,
            shots: list[str] | None = None, expected: str = "", suggestion: str = "",
            automation_suspect: bool = False) -> str:
    """Log one pain point the moment you hit it (out/findings.jsonl).
    severity 4 = stopped me / lost work / wrong film; 3 = cost me minutes or I had to guess;
             2 = friction every time; 1 = polish.
    kind: blocked | confusing | hidden | wrong-result | slow | missing | broken | untrustworthy | polish
    automation_suspect: True when a person with a mouse might not hit this (say why in `happened`)."""
    fp = OUT / "findings.jsonl"
    n = sum(1 for _ in open(fp, encoding="utf-8")) + 1 if fp.exists() else 1
    fid = f"{NAME.upper()}-{n:02d}"
    row = {"id": fid, "at": _stamp(), "editor": NAME, "title": title, "wanted": wanted, "did": did,
           "happened": happened, "expected": expected, "severity": severity, "kind": kind, "where": where,
           "shots": shots or [], "suggestion": suggestion, "automation_suspect": automation_suspect}
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("FINDING", fid, f"[sev {severity}, {kind}]", title, flush=True)
    return fid


def delight(title: str, why: str, shots: list[str] | None = None) -> None:
    """Something that worked better than you expected: say so, so nobody breaks it."""
    with open(OUT / "delights.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": _stamp(), "editor": NAME, "title": title, "why": why, "shots": shots or []},
                           ensure_ascii=False) + "\n")
    print("DELIGHT", title, flush=True)


def truth(path: str):
    """Read-only peek at the server (GET) to check that what the screen says is so. Never a way
    to do a step. Every peek is written to the journal."""
    import urllib.request
    note(f"peeked at {path} to check the screen's claim")
    with urllib.request.urlopen(URL.rstrip("/") + path, timeout=30) as r:
        return json.loads(r.read().decode() or "null")
