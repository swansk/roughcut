# Cut board — the app wrapper

The human's half of the loop. Karl, after watching the first two cuts:

> *"For the editor to be truly compelling (final product), it should provide an easy to use
> interface for the human to interject / ask for edits / set the scene and story… What you will
> need to do when you go to app is make that fine tuning **fun and easy**."*

## Run it

```
uv run app/server.py --footage ~/footage/copper-02-2026
```

**Run it inside WSL, from a login shell.** `uv`, `ffmpeg` and `~/footage` all live there;
PowerShell cannot see any of them, and a non-login shell misses `~/.local/bin`. From Windows:

```
wsl -d Ubuntu -- bash -lc "cd /mnt/c/Users/karl/Documents/Projects/roughcut && \
  uv run app/server.py --footage ~/footage/<bin>"
```

Then open `http://localhost:8765` in Windows — WSL2 forwards localhost, so the browser side needs
nothing.

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
- **Only the monitor streams.** A browser gives a host about six connections. The shot cards
  used to be `<video>` elements, so a 16-shot cut opened eighteen streams and the monitor's own
  request queued behind them: measured from `playFrom(0)` on Killington, the first frame
  arrived at **6.8–9.5 s** while the audio had already started — Karl saw a blank monitor and
  heard the cut. Cards are `<img>` posters cut from the proxy at the in-point
  (`/media/poster/<stem>.jpg?t=<in>`, a few KB each, cached under `--work` and immutable), so a
  page load moves **93 KB** of media rather than opening nineteen streams.
- **The monitor fetches the shot, not the top of the file.** Its two elements are
  `preload="metadata"` and `arm()` puts the in-point in the URL as a `#t=` media fragment. Told
  to preload everything with no idea where the shot starts, Chrome downloads from byte 0: a
  shot playing at 188.2 s had `0–15 s` buffered and its seek queued behind that download. Now
  the buffered range is the shot's, and the first painted frame lands **~0.9 s** after play.
- **Review copies.** A finished render is the master — `delivery` writes 4K at ~44 Mbps, 987 MB
  for three minutes — and the A/B players used to stream it: 157 MB pulled in 15 seconds of
  watching, against 5.3 MB for a 720p copy of the same cut. Every render gets one of those
  under `--work/reviews/<bin>/`, derived in the background after the render reports done and
  one at a time. The players play the copy; **Download** on each row serves the master with
  `content-disposition: attachment` and a name that says which bin, how many shots, how long
  and at what quality.

Everything else follows: edits are local and instant, only Save / Snap / Render touch the
server, and every edit is undoable because fiddling is only fun when it is cheap to be wrong.

## What's on screen

| | |
|---|---|
| **Monitor** | The whole cut, playing from the proxies — shot after shot, no render. A strip under it shows every shot as a block, width to length, coloured by clip; click one to play from there. `space` plays / pauses from the selected shot, `enter` plays just that shot, and the poster on any card jumps the monitor to it — which scrolls into view, and writes a refused play or a media error on its screen rather than sitting silent |
| **Project** | Where this bin is: clips in the folder, how many analysed, how many looked at, shots in the cut, and the two analysis passes with progress bars. The audio pass is local and free; the visual pass costs model calls, so it is offered with a count and a price while there is footage nobody has looked at, and never runs on its own |
| **Timeline** | One card per segment: a still of the frame at the in-point (a few KB, not a stream — see the latency section), the transcript lines that fall inside the cut, why it was chosen (editable), trim controls, drag to reorder. Trim the in-point and the still follows once the trimming settles |
| **Boundary warnings** | A live ⚠ when a cut opens mid-sentence or clips a line off — the defect Karl flagged, surfaced while you trim rather than only when you ask |
| **Story panel** | Free text saved into the EDL. The thing the agent is worst at; typing "the milk is the running joke" beats an hour of analysis |
| **Music** | Pick a track from `assets/music/`, set how far it ducks under speech and its fades. Saved into the EDL as `effects_music` the moment it changes, rendered by `assemble.py` with the picture untouched, and heard under the monitor with the same duck before you render |
| **Add a moment** | Two tabs: **heard** — audio candidates not already in the cut, ranked — and **seen** — what the visual pass found, ranked by the bin's `events.json` (kind × corroboration from the motion and onset tracks × a closer look's confirmation), with unconfirmed claims sealed as such. One click to insert |
| **Ask for a change** | Plain-language note → revised timeline, shown as a diff you accept or discard |
| **Snap to speech** | Runs `edl_snap.py` and shows the result as a proposal — undoable, never silently applied |
| **Steps** | footage · analyse · first cut · refine · render, with the one you are on marked. Read from state, so it cannot drift from the files on disk |
| **Renders** | Runs `assemble.py` in the background, then keeps every version — newest first, loadable into two players side by side, because judging an edit is comparative. A select chooses the profile: `preview` (1080p, fast) or `delivery` (native frame rate, up to 4K, slow/CRF 18 — about 17 minutes for a 3-minute cut). Renders that match the cut on the board are marked **this cut**; a proposal rendered without being accepted says so. Each row states its size and resolution and carries a **↓ download** that saves the master under a real name; the players stream a 720p review copy instead, and say so while one is still being made |

### Ask — steering by asking rather than dragging

"tighten the intro" · "more skiing, less airport" · "build it around the milk joke".

**With an empty timeline the same call originates the cut.** That is deliberately not a separate
button: "ask for what you want" should not change its name depending on whether anything is on
screen yet. It is the piece that removes the hand-authored-EDL prerequisite — a bin of analysed
footage plus a sentence about what the film is for is now enough to get a first cut.

**If the footage has been looked at** (the Project panel's *Look at the footage*, or
`research/tools/visual_pass.py`; sidecars per bin under `--work`, or `--visual`), each clip also
arrives with *what is visible* — moments read from sampled frames, with the notable ones marked
and the unusable stretches flagged. That is the only account the model has of things nobody
narrated, and the same moments show on the shot cards and under *Add a moment → seen*. It is
optional: the audio pass is local and cheap, this one costs model calls, which is why it is
priced before it is offered.

The originating prompt states two things a model reading clips one at a time cannot rediscover,
both measured in R8/R9 rather than guessed: transcript density points *away* from the action on
this footage (the camera is on the person doing the thing), and the connective tissue is usually
a running joke rather than a topic. It is also told plainly that it cannot see the frame, and
that its `why` is what the human checks the reasoning against.

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

Keys: `j`/`k` move · `space` play / pause the cut from here · `enter` play this shot only ·
`[` `]` trim in · `{` `}` trim out · `x` remove · `u` undo · hold `shift` for 1s steps.

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

**166 tests, ~2 min** (138 API + 28 driving real Chromium). The suite builds its own three-clip
synthetic project with fabricated transcripts, so it is fast, deterministic, and does not depend
on `~/footage` — which matters for the container target. Model calls run against a scripted
backend; there are no live calls.

The two layers answer different questions. The API tests prove the endpoints behave. They
cannot prove that *using* the board works — that trimming updates the total, that undo restores
exactly, that a preview can seek, that the boundary warnings track reality, that an empty
timeline leads somewhere. Those live in the JavaScript and the browser's media stack, so twenty-eight
tests drive real Chromium against a real uvicorn server.

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

- **An originated cut is loosely snapped.** 12 of 20 out-points landed on an utterance end in
  the live first-cut run, against 17 of 18 when revising a cut that had already been snapped by
  hand. Snap to speech fixes it in one click, but the first proposal reads rougher than a
  revision does, and nothing yet runs snap automatically on an originated plan.
- **Junk and orientation are still not proposed.** The app analyses whatever is in the folder;
  which clips are junk, and whether the bin's rotation metadata should be honoured, are decided
  outside it (`--orient` at scaffold time, `skip` on the analyse call).
- **Music mode (P2.6) is not built.** A bed under the cut is (the Music panel); cutting *to* a
  track is not. The slot-driven contract it implies ("fill these N slots of these lengths") is a
  different selection problem, filed in FUTURE_PHASES. Nothing aligns a cut to a beat yet — a
  cut that lands on one does so by luck.
- Single project per launch; no project picker, though a bin no longer needs an EDL to open.
- `complete_many` exists per SPEC §6.1 but nothing batches yet; the CLI backend would just loop.
