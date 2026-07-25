# Cut board — the app wrapper

The human's half of the loop. Karl, after watching the first two cuts:

> *"For the editor to be truly compelling (final product), it should provide an easy to use
> interface for the human to interject / ask for edits / set the scene and story… What you will
> need to do when you go to app is make that fine tuning **fun and easy**."*

## Run it

```
uv run app/server.py --footage ~/footage/copper-02-2026
```

A folder of footage is the only required argument. If that bin has no EDL yet, one is
scaffolded empty under `--work` (`~/work/app/projects/<bin>.edl.json`) and sidecars default to
`~/work/app/audio/<bin>` — so opening the board no longer requires hand-authoring JSON in a
terminal first.

To open an EDL and sidecars that already exist, name them:

```
uv run app/server.py --footage ~/footage/copper-02-2026 \
  --edl research/edl/B1-variantB.json --sidecars ~/work/audio
```

Then open `http://localhost:8765`. First launch builds a 720p proxy per clip (a few minutes,
once); later launches are instant.

`--orient auto|none` applies only when a *new* project is scaffolded. It is per-bin and cannot
be generalised — Copper's rotation side-data is spurious (`none`), Killington's is correct
(`auto`) — so it is asked for rather than guessed.

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
| **Project** | Where this bin is: clips in the folder, how many analysed, shots in the cut, and the audio pass with a progress bar. Analysis was step 2 of five terminal steps; it runs here now |
| **Timeline** | One card per segment: preview parked on the in-point, the transcript lines that fall inside the cut, why it was chosen (editable), trim controls, drag to reorder |
| **Boundary warnings** | A live ⚠ when a cut opens mid-sentence or clips a line off — the defect Karl flagged, surfaced while you trim rather than only when you ask |
| **Story panel** | Free text saved into the EDL. The thing the agent is worst at; typing "the milk is the running joke" beats an hour of analysis |
| **Add a moment** | Audio candidates not already in the cut, ranked, one click to insert |
| **Ask for a change** | Plain-language note → revised timeline, shown as a diff you accept or discard |
| **Snap to speech** | Runs `edl_snap.py` and shows the result as a proposal — undoable, never silently applied |
| **Render** | Runs `assemble.py` in the background and plays the result inline |

### Ask — steering by asking rather than dragging

"tighten the intro" · "more skiing, less airport" · "build it around the milk joke".

The model is given every clip's **transcript**, the current edit, the story text and the target
length, and returns a full revised edit. It arrives as a **proposal**: a diff, with Accept and
Discard, undoable once applied. A model edit that applied itself is how an editor learns to stop
trusting the tool.

Plans are validated hard before they reach the screen — a segment naming a clip that doesn't
exist, or running past the end of one, fails and gets one bounded re-ask. An invented timestamp
that renders as missing footage is worse than a visible error.

All of this goes through `roughcut.inference` (SPEC §6), never directly to a model: roles from
`config.py`, `ROUGHCUT_BACKEND=claude_cli|anthropic_api`, and every call logged with tokens and
`projected_usd` — including on the Max subscription, where there is no marginal cost but the
projection is what answers "would this be affordable in production".

**Requires auth.** The CLI backend needs `claude /login` inside WSL; the API backend needs
`ANTHROPIC_API_KEY` and `ROUGHCUT_BACKEND=anthropic_api`. Until then Ask returns a 502 that says
exactly that — the UI surfaces it rather than failing silently.

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

**50 tests, ~20s.** The suite builds its own three-clip synthetic project with fabricated
transcripts, so it is fast, deterministic, and does not depend on `~/footage` — which matters
for the container target. Model calls run against a scripted backend; there are no live calls.

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

- **Ask is unproven against a live model.** The wiring, validation, retry, accounting and UI are
  all tested against a scripted backend, but no real revision has been generated — the CLI in
  WSL is not logged in. Whether the proposals are any *good* is unmeasured.
- **Music mode (P2.6) is not built.** Karl asked for supplying an audio track and cutting to it
  as an available, non-default mode. The slot-driven contract it implies ("fill these N slots of
  these lengths") is a different selection problem, filed in FUTURE_PHASES.
- Single project, single EDL per launch; no project picker.
- The render list is not browsable — the newest render is shown, older ones live on disk.
- `complete_many` exists per SPEC §6.1 but nothing batches yet; the CLI backend would just loop.
