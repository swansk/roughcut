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
  under `--work/reviews/<bin>/`, one at a time and **as the render's last phase** rather than
  as an errand after it — the players stream the copy, so a job that reads 100% while the copy
  is still being made is telling the truth about ffmpeg and a lie about the wait. Measured on
  a 181 s cut: 39.7 s off the 1080p preview master, 95.4 s off the 4K delivery one, which is a
  fifth and a twelfth of their renders. Renders from before the copies existed are built lazily
  off the versions list instead. The players play the copy, fall back to the master and say so
  when one failed; **Download** on each row serves the master with
  `content-disposition: attachment` and a name that says which bin, how many shots, how long
  and at what quality.

Everything else follows: edits are local and instant, only Save / Snap / Render touch the
server, and every edit is undoable because fiddling is only fun when it is cheap to be wrong.

## What's on screen

| | |
|---|---|
| **Bin · cut** | The header's name, on every screen: which bin the board is on and which cut of it. Click it (or `O`) for one panel — the cuts of this bin, and the bins the board knows (opened before, or a folder of video next door; a path typed in opens any other). **Save copy** writes the whole project file under a name — timeline, story, music and the pass's picks, so nothing ever merges and `assemble.py` renders a copy exactly as it renders the original — and moves to it, or stays where you are as a checkpoint. A row opens a cut; *rename* keeps the file where it is (the name lives inside it); *delete* moves it to the bin's `trash/`, never the one on the board. Copies live under `--work/projects/<bin>/`; the bin's own file stays where it was. The board flushes its autosave before any switch, a bin reopens on the cut it was left on, and a switch is refused while a job runs — a render or an Ask belongs to the cut it started on. Renders record their cut and the download name carries it. The control is `switcher.js`, shared by the three screens |
| **Progress strip** | One bar under the header that every long operation drives — label, bar, percentage, elapsed, ETA and a line saying what it is doing right now. It holds two at once (a render and an Ask overlap routinely), re-attaches to whatever is still running after a reload, and is not there at all when nothing is. See "One bar for everything" |
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
| **Renders** | Runs `assemble.py` in the background, then keeps every version — newest first, loadable into two players side by side, because judging an edit is comparative. A select chooses the profile: `preview` (1080p, fast) or `delivery` (native frame rate, up to 4K, slow/CRF 18 — about 17 minutes for a 3-minute cut). Renders that match the cut on the board are marked **this cut**; a proposal rendered without being accepted says so. Each row states its size and resolution and carries a **↓ download** that saves the master under a real name; the players stream a 720p review copy instead, and say so while one is still being made. **One at a time**: a second Render is refused with a 409 naming the running job, and the control reads *Rendering…* until it is over — two encodes on one box make both crawl |

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

### One bar for everything

Four long operations used to have four unrelated notions of progress, so no single bar could
exist. They share one now (`roughcut/progress.py`): a job record with `kind, label, state,
started, elapsed_s, pct, detail, eta_s` and an ordered list of `milestones` carrying weights and
`done_at`. `pct` comes from milestones where an operation has them and from **counted work on
disk** where it does not — parts written by `assemble.py`, sidecars written by the two passes —
because the filesystem is the honest progress bar. It never dips and never reaches 100% before
the job is over. `GET /api/jobs` is the heartbeat the strip polls; `GET /api/job/{id}` is the
whole record, plan and log included.

**An Ask sizes itself before it runs.** One cheap `ROLE_ANALYSIS` call returns `{eta_s,
milestones}` — the checkpoint *keys* are the app's, because a milestone nothing can observe
completing is a bar that stops moving, and the labels, weights and ETA are the model's. It is
validated the way a plan is (bounded count, positive weights, sane ETA, one re-ask) and it can
never block the work: anything that goes wrong falls back to a measured estimate. The state goes
`estimating → running → done`.

The milestones are then completed from what the model has **actually written**. `claude -p`
returns once, at the end, so `ClaudeCliBackend` takes an optional `on_partial` and switches to
`--output-format stream-json --verbose --include-partial-messages`; the closing `result` object
is identical to the non-streaming one, so the ledger and `projected_usd` are unaffected. A plan
is a JSON list of segments, so the bar can say "12 of ~18 shots decided". Each completed
milestone recalibrates the ETA from elapsed-vs-expected.

Measured on Killington (12 clips, a 17-shot cut), and the numbers the defaults come from: the
estimate call is 15–18s and $0.034–0.037 (most of the 28k input tokens are the CLI's own
prompt, not ours); the Ask itself is 79–118s and $0.38–0.46; the phases split **read 5% ·
think 72% · shots 19% · notes 3% · polish 1%**, which is why the estimator is told that rather
than left to guess; a 181s preview render is 0.89× real time with the join 5% of it. A render
is three weighted phases — **cutting, joining, review copy** — and reaches 100% only when the
copy the players stream exists or has definitively failed. The analyse, visual and render jobs
take **computed** estimates rather than model ones — their
length is arithmetic the app already does, so a call there would spend money to be less
accurate.

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

**204 tests, ~2 min** (173 API + 31 driving real Chromium). The suite builds its own three-clip
synthetic project with fabricated transcripts, so it is fast, deterministic, and does not depend
on `~/footage` — which matters for the container target. Model calls run against a scripted
backend; there are no live calls.

The two layers answer different questions. The API tests prove the endpoints behave. They
cannot prove that *using* the board works — that trimming updates the total, that undo restores
exactly, that a preview can seek, that the boundary warnings track reality, that an empty
timeline leads somewhere, that the progress strip tracks a call still being written and picks it
back up after a reload. Those live in the JavaScript and the browser's media stack, so thirty-one
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
- Three live Asks on Killington, watched end to end. The last one: estimate 16.1s / $0.0351 /
  predicted 80s against an actual **79.1s**; the checkpoints landed within four points of the
  predicted split; the bar ran 0 → 4 → 22 → 66 (the creep cap through the long think) → 78 → 96
  → 100 without ever going backwards; "17 of ~17 shots decided" against a plan that came back
  with exactly seventeen; the ETA read 2.6s left with 3.0s actually left. A 17-shot, 181s
  preview render took 160.8s and produced 181.11s of video (+0.13s drift), and one real visual
  sheet reported "1 of 1 clip seen — reading CLIP_05 — 1 sheet" the whole way through.

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
- **A cut is the whole EDL, and the pass's verdicts live in it.** Keeps, rejects and notes made
  on the floor while on one cut are that cut's; another cut of the same bin does not see them.
  Right for "try different things", and a seam if the floor is ever worked on across cuts — the
  fix then is to move `selects` / `floor` / `look` into a per-bin file, not to merge on switch.
- `complete_many` exists per SPEC §6.1 but nothing batches yet; the CLI backend would just loop.
