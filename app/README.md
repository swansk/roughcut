# Cut board — the app wrapper

The human's half of the loop. Karl, after watching the first two cuts:

> *"For the editor to be truly compelling (final product), it should provide an easy to use
> interface for the human to interject / ask for edits / set the scene and story… What you will
> need to do when you go to app is make that fine tuning **fun and easy**."*

## Run it

```
uv run app/server.py --edl research/edl/B1-variantB.json \
  --footage ~/footage/copper-02-2026 --sidecars ~/work/audio
```

Then open `http://localhost:8765`. First launch builds a 720p proxy per clip (a few minutes,
once); later launches are instant.

## What it is, and what it deliberately is not

**It is a view over the EDL file**, not a new source of truth. The same JSON that `assemble.py`
renders and `edl_snap.py` rewrites is what the board reads and saves. Anything done by hand here
stays scriptable, and anything done by script shows up here on reload. That is why there is no
database and no project format.

**It is not an NLE.** No effects, no transitions, no multi-track. The one job is deciding *which
moments, in what order, cut where* — which is the part the agent gets wrong and a human fixes in
seconds.

## The design constraint is latency

"Fine-tuning is fun" reduces almost entirely to "the loop is tight". Two consequences shape the
code more than anything else:

- **Proxies.** The browser never touches the 5.3K HEVC masters — 7.6 GB of Copper becomes 85 MB
  of 720p H.264, about 90× smaller. Seeking a proxy is instant; seeking a master is not, and an
  editor that stutters on every scrub is one nobody opens twice.
- **Byte-range serving.** Not a nicety: without HTTP 206 support a `<video>` element cannot seek
  at all, only stream from zero, so per-segment preview would be useless however small the proxy.

Everything else follows: edits are local and instant, only Save / Snap / Render touch the
server, and every edit is undoable because fiddling is only fun when it is cheap to be wrong.

## What's on screen

| | |
|---|---|
| **Timeline** | One card per segment: preview parked on the in-point, the transcript lines that fall inside the cut, why it was chosen (editable), trim controls, drag to reorder |
| **Boundary warnings** | A live ⚠ when a cut opens mid-sentence or clips a line off — the defect Karl flagged, surfaced while you trim rather than only when you ask |
| **Story panel** | Free text saved into the EDL. The thing the agent is worst at; typing "the milk is the running joke" beats an hour of analysis |
| **Add a moment** | Audio candidates not already in the cut, ranked, one click to insert |
| **Snap to speech** | Runs `edl_snap.py` and shows the result as a proposal — undoable, never silently applied |
| **Render** | Runs `assemble.py` in the background and plays the result inline |

Keys: `j`/`k` move · `space` play · `[` `]` trim in · `{` `}` trim out · `x` remove · `u` undo ·
hold `shift` for 1s steps.

## Tests

```
# API layer — no browser needed, runs anywhere
uv run --with pytest --with fastapi --with uvicorn --with httpx pytest app/tests -q

# plus the browser layer (skipped automatically if playwright is absent)
uv run --with playwright playwright install chromium
bash app/tests/install_browser_deps.sh     # no-sudo system libs, one-off
source app/tests/browser_env.sh
uv run --with pytest --with fastapi --with uvicorn --with httpx --with playwright \
    pytest app/tests -q
```

**29 tests, ~18s.** The suite builds its own three-clip synthetic project with fabricated
transcripts, so it is fast, deterministic, and does not depend on `~/footage` — which matters
for the container target.

The two layers answer different questions. The API tests prove the endpoints behave. They
cannot prove that *using* the board works — that trimming updates the total, that undo restores
exactly, that a preview can seek, that the boundary warnings track reality. Those live in the
JavaScript and the browser's media stack, so ten tests drive real Chromium against a real
uvicorn server.

`install_browser_deps.sh` exists because `playwright install --with-deps` needs root and this
box has no passwordless sudo; it resolves the packages through apt and unpacks them under
`~/.local`, the same no-sudo pattern used for ffmpeg and uv.

## Verified

Driven through a real browser against the real bin, not just unit-tested:

- Snap applied from the UI took variant B from 2:16.6 to 2:42.5 and drove the live boundary
  warnings from **5 to 0**; undo restored the exact prior state and brought all five back.
- Render triggered from the UI produced **162.68s** — identical to the command-line render of
  the same EDL — with no rotation side data and video+audio streams only.
- Media endpoint returns `206 Partial Content` with a correct `content-range`.

Three real defects were found by writing the tests rather than by using the app:

1. **Proxies were served while still being written**, so a `<video>` got a truncated stream and
   cached the failure until reload. Now built to a `.part.mp4` and renamed atomically.
2. **`edl_snap` grew silent segments.** A shot deliberately chosen as a quiet beat — a ski pass,
   a held landscape — would reach forward and absorb the next utterance, turning it into
   dialogue nobody asked for. Closure now requires the segment to already carry speech.
   Re-verified against the real B cut: byte-identical output, so the fix corrected a latent
   footgun without changing the current edit.
3. **The proxy filter upscaled small sources**, spending bytes on detail that isn't there.
   Capped by width instead, which leaves anything under 1280 alone.

## Known gaps

- **Music mode (P2.6) is not built.** Karl asked for supplying an audio track and cutting to it
  as an available, non-default mode. The slot-driven contract it implies ("fill these N slots of
  these lengths") is a different selection problem, filed in FUTURE_PHASES.
- **No agent round-trip yet.** The story panel captures intent but nothing re-runs selection
  from it — the human can currently steer by hand, not by asking. That is the natural next step
  and the thing that would make "interject" literal.
- Single project, single EDL per launch; no project picker.
- The render list is not browsable — the newest render is shown, older ones live on disk.
