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

**Models and the CLI.** Deep work (the Ask, the close look and the audit, effect design, the
model's Find, the themes) runs on Opus 5.5; quick work (the coarse sheets, the estimates, the
probe) on Sonnet 5.5 — `config.DEEP_MODEL` / `QUICK_MODEL`, each role still overridable by
`ROUGHCUT_MODEL_<ROLE>`. **Opus 5.5 needs Claude Code 2.1.280 or newer** (`claude update` in
WSL). When the CLI needs you — signed out, too old for a model, a tool blocked, the plan's
usage window full — every screen shows a banner across the top with what is wrong, the one
command to run in a WSL terminal (Copy) and *Check again*. It is fed by the free checks at
launch (`claude --version`, `claude auth status`), by the probe (one tiny call per distinct
model), and by any job whose call fails that way, the visual pass's subprocess included.
`GET /api/backend` is what it polls; `POST /api/backend/probe` re-checks everything.

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
| **Dock** | The right column (INTAKE M11): a rail of tools — **Bin · Ask · Sound · Out** — and one panel exactly the viewport's height that scrolls inside itself, so the page never scrolls for it and the monitor never leaves. One tool open at a time, remembered per browser; the rail's badges count keeps and versions. A new tool is one rail button and one `<section data-tool>`; `dock.open(name)`, `dock.reveal(el)` and `dock.badge(name, n)` are the whole API (`dock.js`). Keys are an overlay on `?` (the timeline's map renders into it); project facts are the *Project ▾* popover |
| **Progress strip** | One bar under the header that every long operation drives — label, bar, percentage, elapsed, ETA and a line saying what it is doing right now. It holds two at once (a render and an Ask overlap routinely), re-attaches to whatever is still running after a reload, and is not there at all when nothing is. See "One bar for everything" |
| **Monitor** | The whole cut, playing from the proxies — shot after shot, no render. The timeline under it (next row) is where you scrub and select. `space` plays / pauses from the selected shot, `enter` plays just that shot, and the still in the inspector plays the cut from it — the monitor scrolls into view, and writes a refused play or a media error on its screen rather than sitting silent |
| **Timeline** | Under the monitor, in place of the old strip (INTAKE M9, `timeline.js` — `window.tl`): a ruler with labelled ticks whose step follows the zoom, a scrollable viewport, a V1 lane of shot blocks placed by film time — a still at the in-point, the clip's colour, the stem, the duration and the shot's strongest line, a mark on any edge that opens mid-sentence or cuts a line off — and the playhead as one line across the whole cut. `+` / `−` zoom (⌘ + wheel around the cursor), `\` fits, ⇧ + wheel pans. Click a block to select it and play from there; ⇧-click a range, ⌘-click to toggle; click or drag on the ruler to scrub — the monitor parks there, paused, and `space` resumes from it. Every shot has a stable id (blocks carry it, never a position). One undo / **redo** stack for the whole board — `u` or ⌘Z, ⌘⇧Z, the header's Undo / Redo with the edit's name in the tooltip. Trims by drag, the magnet, JKL and the other lanes are I9.2–I9.4, built on the module's API (the comment block at the top of `timeline.js`) |
| **Project ▾** | A popover under the header: where this bin is — clips in the folder, how many analysed, how many looked at, shots in the cut, the index's state and a link to the screen that runs it, and one line about the bin (`bin · 7 moments · 2 heroes · 1:12 if strung out`) |
| **Inspector** | One panel under the timeline for the selected shot (INTAKE M9, I9.5) — the list of one card per shot is gone. `SHOT 8 of 21 · CLIP_03 · 0:18.1 → 0:21.2 · 3.1 s`, the boundary warning, where it starts in the film; a still of the frame at the in-point (a few KB, not a stream — see the latency section) that follows a trim once the trimming settles; the transcript lines and what the visual pass saw inside the cut; why it was chosen (editable, saved with the cut); a proposal's polish and the shot's act when the segment carries them; ±0.25 s trims (⇧ for 1 s, each one undo entry), *▶ play* this shot only, *✎ ask about this shot* (the scoped ask) and *remove*. A multi-selection shows `3 shots selected · 12.4 s` and remove for all; no selection says so and gives the cut's totals |
| **Boundary warnings** | A live ⚠ when a cut opens mid-sentence or clips a line off — the defect Karl flagged, surfaced while you trim rather than only when you ask |
| **Story** | In the Ask tool. Free text saved into the EDL. The thing the agent is worst at; typing "the milk is the running joke" beats an hour of analysis |
| **Sound** | The music bed, its own tool: pick a track from `assets/music/`, set how far it ducks under speech and its fades. Saved into the EDL as `effects_music` the moment it changes, rendered by `assemble.py` with the picture untouched, and heard under the monitor with the same duck before you render. A click on the music lane opens it |
| **Bin** | The dock's default tool (INTAKE M11). **kept** — the bin, first and the one the board opens on when the pass has kept anything: a card per keep, heroes first then by clip and start — the still at its start, `CLIP_04 · 3:44.0 → 3:48.2 · 4.2 s`, `★ HERO`, the pass's reason and the editor's note, the pass's labels as chips, and either *in the cut · shot N* (click selects the shot) or *+ add*; a keep whose footage left says so and cannot be added; an empty bin points at the pass. The chips above the grid are the same labels as a filter, with counts, plus hero / in the cut / not yet — one chip on at a time, the same chip again clears it, a chip on a card works the same. The search box filters the grid as you type; **Find** and **Ask the model** search the whole footage from the same box (word match over transcripts and what the visual pass saw; the priced model read for what words cannot reach), with each match playing the whole clip proxy seeked to the moment. Click a card to select it; `enter` adds it, `space` or a double-click plays the clip in the bin's own player so the monitor stays on the cut; drag a card onto V1. **heard** — audio candidates not already in the cut, ranked — and **seen** — what the visual pass found, ranked by the bin's `events.json`, with the priced *Audit N claims* button above it. Re-read when the tab is shown and after every save while it is up. The rail's badge is the count of keeps |
| **How the machine saw it** | A strip per clip (INTAKE M15, `deep.js`): the clip's length as a bar with a lane per layer of the index — **heard** (the utterances), **coarse** (a tick per sheet frame), **close** (the close look's windows and their 1 s frames), **deep** (each deep look's span and its keyframes; the frames it asked for in orange), **motion** (the free 10 Hz track as a sparkline) — and the **claims** each layer made, coloured by evidence (confirmed green, deep gold, unaudited outlined, close look only, contradicted red, unusable hatched). Under it the legend says the granularity in words, from each sidecar's own record — *a frame every 4 s, read 30 to a sheet (6×5, 320 px) by Sonnet 5.5* — and says *model not recorded* (or *frame times derived from the interval*) for sidecars written before those were kept, rather than guessing from today's config. Hover: what was seen at that second (the nearest coarse frame and how far, the close window, the deep span, the claims). Drag: choose up to 20 s to look deeper at. In the inspector for the selected shot (its range marked, *Look deeper* priced for it), under the pass's tape for the pick's clip (the playhead on it; no buttons — the pass is keys, so there it shows and says the board is where to look deeper; a click on a deep span opens it, again closes it), and as a mini strip on each card of the open screen |
| **Junk** | Proposed by a measurement, confirmed by you (HANDOFF roadmap item 5, `roughcut/junk.py`). From what the index already has — the clip's colour file (one sample per 5 s of the proxy), the audio sidecar's words, the duration — a clip is proposed when it is **essentially black** (mean luma < 12/255 and no sample above 40), **one flat field** (luma spread < 10/255 — a lens cap, a bag) or **under 1.5 s**, and never when a single word was heard. No model call. A proposed clip has a dashed card at the head of the bin grid with its reason, and its keeps wear `junk?`; **Confirm** takes it out of the Ask's inventory (first cut and revision — unless a shot of the cut still uses it), out of Find, out of the grid's default view and the heard / seen rows, and the index journal skips its look and close look — the money; **Keep** overrides the proposal. The `junk N` chip shows proposed and confirmed clips together, a confirmed one with **Keep** to undo. While the index runs, a proposed clip's sheets wait behind every clean clip's, so an answer given mid-run still saves them. `/open`'s cards carry `junk?` / `junk`. **API:** `GET /api/junk` — every footage clip with `state` (`proposed` · `confirmed` · `kept` · `clean`), the machine's `proposed`, the `verdict`, `reasons` (sentences) and `numbers` (the facts and the thresholds); a clip with a proxy and no colour file is measured on the way. `POST /api/junk {clip, verdict: "junk" \| "keep" \| null}` — writes the EDL's `junk` block (only the human's word is stored; proposals are recomputed, cached by file mtimes) and the journal when no index is running (`index: "applied"`, `stages` it skipped or reopened; a running index picks it up before its next stage). `PUT /api/project` validates a `junk` block like the other blocks. The thresholds are **provisional** — one bin's numbers (B1/Copper's `benchmarks/labels/B1-luma.json`) |
| **Ask for a change** | Plain-language note → revised timeline, shown as a diff you accept or discard |
| **Cut from the bin** | The same Ask with a fixed note — *build the cut from the editor's selects: every hero must appear, use the other keeps where they serve the story, and take nothing else unless it is needed to make a keep land* — and the current story, then the usual proposal / accept / discard. Under the note in the Ask panel, and beside *Ask for a first cut* when the timeline is empty (where the panel is hidden, and where someone arriving from the pass lands); disabled with a hint while the bin is empty. The fixed note never becomes the story |
| **Snap to speech** | Runs `edl_snap.py` and shows the result as a proposal — undoable, never silently applied |
| **Flow bar** | One bar, the same on all three screens (INTAKE M14, `/flow.js` from `GET /api/flow`), in place of the open screen's six numbered steps, the board's five-step strip and its `open · pass · board` pills; the pass had none. Seven stages in the order a film is made — **Footage · Index · Brief · Pass · Cut · Polish · Render** — each with one state: ✓ *done* (green), *running* (a thin fill under it, the bar polls every 1.5 s while anything runs, 5 s otherwise), *ready*, *waiting* (dashed; the hover says on what), **needs you** (amber — a proposal to answer, looks paused on your word) or *optional* (dashed — the brief, the pass before a first cut, polish). A few characters after the name are the count it rests on (`Index 54%`, `Pass 3/12`, `Render stale`); the hover is the sentence (`Indexed — heard 12/12 · looked at 12/12 (a frame every 4 s) · close looks 3/12`). This screen's stages are underlined. A click goes to the screen where the stage is done — on the board with its dock tool open (`/#tool=ask`, `#tool=fx`, `#tool=out`; the board honours the hash) and without a reload when it is already there. **Next**, at the right end, is the one recommended action: a proposal waiting first (it is already paid for), then the index until every clip is heard, the first cut (from your keeps when there are any), a render of a cut that changed, then paused looks with their price. A priced action only navigates to the button that carries its price; a free one on its own screen is pressed (*Render the cut* clicks the board's Render). With the CLI signed out, Next is the CLI's fix and the priced stages wear an amber edge — the banner above says the command. **API:** `GET /api/flow` → `stages` (`key, name, state, summary, screen, tool, href, counts`, and `progress` / `needs {reason, action}` / `waiting_on` + `why` / `blocked`), `next` (`stage, sentence, verb, screen, tool, href, kind` — `go` · `wait` · `cli` — and `click` / `usd` / `command`), `blockers` (the server's own `backend_fix`, the stages it blocks), `running`. `POST /api/asks/answer {answer: accept \| discard}` marks the newest Ask proposal answered (the board's Accept and Discard call it), so a discarded proposal stops waiting on you. The model is `roughcut/flow.py`, pure; `server.flow_facts()` reads the files. The open screen says the same in its own panels: a clip whose free stages are done while the looks are paused is *on the pass · look paused* (it used to read *queued* under a footer saying every clip was released — `journal.progress()` rows now carry `waiting`), its card's flag is *on the pass*, and the paused box says what waits and what it costs — *Looks are paused — 9 clips, ~$X*, **Resume · ~$X** — with the reason only when the app wrote it (the budget cap, or you); `GET /api/index` carries the price as `waiting` |
| **FX** | Effects the model designs and the machine verifies (INTAKE M12), a tool in the dock for the selected shot. Say what the effect is — *hit markers where my skis hit the rocks, with the sound* — or press *Draw a reference*, mark the frame and write the goal; **Design** (the price on the button; *place on the frames* adds a priced look at a strip of frames to put the anchor on the skis) returns a *proposal*: a card with the hits (`2:42.8 · x 0.50 y 0.72`, ◀ ▶ nudge a frame; a hit selected and a click on the paused monitor moves its anchor), the model's reason, and the buttons. **Preview** plays the shot with the effect drawn over the monitor and heard; **Iterate** takes a note (*red and bigger*); **Verify** renders the one shot from its proxy with and without the effect and runs the checklist — every hit inside the shot, every anchor inside the frame, sound and marker starting together, each hit on an onset peak, a measured ≥ 6 dB rise and a measured picture change at every hit; **Accept** writes it into the EDL's `effects` (the render bakes it into that shot's part); **Discard** / **Remove**. Beside or instead of a picture, a proposal can carry **changes to the cut** (INTAKE M13): slow motion over a range, extending a shot, a generated black / colour / freeze-frame clip, a split, an insert; the card lists them in words, Preview shows the proposed cut on the ghost lane, Accept applies them in one write (and the board reloads to read the new shots). A shot's **speed** shows as a badge on its block and a row in the inspector (¼× ½× 1× 2× or a number); film time everywhere is the range divided by it. The effect is a small program the model writes in a closed vocabulary — a sprite of shapes with keyframed scale / opacity / rotation / offset and a flash, a synth patch of tone / noise / click / sweep layers — drawn by code on the monitor and on the master from one JSON; nothing is fetched, nothing is in pixels. See `docs/design/effects-directions.html` for the alternatives |
| **Out** | Renders, their own tool. Runs `assemble.py` in the background, then keeps every version — newest first, loadable into two players side by side, because judging an edit is comparative. A select chooses the profile: `preview` (1080p, fast) or `delivery` (native frame rate, up to 4K, slow/CRF 18 — about 17 minutes for a 3-minute cut). Renders that match the cut on the board are marked **this cut**; a proposal rendered without being accepted says so. Each row states its size and resolution and carries a **↓ download** that saves the master under a real name; the players stream a 720p review copy instead, and say so while one is still being made. **One at a time**: a second Render is refused with a 409 naming the running job, and the control reads *Rendering…* until it is over — two encodes on one box make both crawl |

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
narrated, and the same moments show in the inspector and under *Add a moment → seen*. It is
optional: the audio pass is local and cheap, this one costs model calls, which is why it is
priced before it is offered.

**Audit the claims** (HANDOFF roadmap item 1, R10's follow-up). The coarse pass's jumps and
falls are one reader's guess from frames four seconds apart, and on helmet-cam footage a tilted
horizon reads as a rider upside down — so the top of the *seen* tab is mostly claims nobody has
checked. The tab's **Audit N claims · ~$x** button spends a 1 s close look on exactly those: the
top `AUDIT_CLAIMS` (12) unaudited jump/fall/crash claims across the bin, notable first then by
score, one window per claim (claims close together share one; a claim a read window already
covers is never re-bought), then rebuilds `events.json`. It is off when nothing is unaudited or a
visual pass is running, and it runs as a `visual` job in the top bar. By API:
`POST /api/visual/audit {"n": 12, "dry_run": true}` returns the plan and its price
(`claims`, `windows`, `calls`, `projected_usd`, `eta_s`) without spending; the same body without
`dry_run` starts the job (409 while a pass runs or over the budget cap, 400 when there is
nothing to audit). `/api/status`'s `visual.audit` carries the same price for the button. The
whole-bin pass's second stage now puts each clip's own unaudited claims ahead of its motion peaks
too, inside the same three windows per clip. A row only a close look saw (no coarse claim
agreeing) is marked **one look** and ranks below an unaudited claim (`FINE_ONLY`).

### How the agent sees, and Look deeper (INTAKE M15)

Karl, 2026-10-03: *"make it clearer how the videos are indexed by the agent (e.g. showing
granularity), and make it easier to run deeper keyframe-based analysis (w/ AI interpolating
as needed between frames to really understand what is going on)."*

The index has four layers and each looks at a clip differently: the **audio pass** (every
word timed), the **coarse sheets** (a frame every 4 s by default, 30 to a 6×5 sheet at
320 px, the quick tier), the **close look** (a frame a second over 8–14 s windows, 3×5
sheets at 480 px, the deep tier) and the free **motion scan** (picture change at 10 Hz).
The strip above shows which seconds each one covered; the numbers come from the sidecars.

**Look deeper** is a fifth layer, run by hand on a span the editor chooses
(`roughcut/deep.py`). It is not a denser sheet. It picks **keyframes where the picture
changes** — the motion track's peaks and turns, a uniform floor so a still stretch is never
unsampled, and the rest of the cap on the gaps with the most change in them (≤ 24 frames
over ≤ 20 s) — cuts each as its own 640 px JPEG from the proxy, and hands them to the deep
model **in order, with their timestamps and the motion between each pair as numbers**. The
answer is a beat-by-beat account in which every beat is either **seen** on a named frame or
**inferred** between two named bracketing frames with why; events in the coarse pass's
vocabulary; the camera (mount, and whether a tilted horizon is the camera — *"unless the
ground under the skis says otherwise"*, R10/R11); and what it is unsure of. It may ask for up
to 8 more frames where its inference is weakest; if it does and the budget allows, **one**
follow-up call adds exactly those and takes the final answer — never a loop. An answer that
puts a beat outside the span, cites a frame that does not exist, or infers without two
bracketing frames is refused and re-asked once.

Where: a **Look deeper · ~$x** button on every row of the bin's *seen* tab (the moment ± 2 s),
in the inspector for the selected shot (the shot's in/out, capped at 20 s), and after a drag
on the strip. The price is a dry run and is on the button (the tooltip has the bound with the
follow-up); nothing spends without the click; one at a time; refused past the budget cap. A
span already read says *Show the deep look* and costs nothing. The run is a `deep` job in the
top bar. The result shows in place: the keyframes as a filmstrip with their timestamps (a
click enlarges one; the frames it asked for are outlined), a bar of beats under it — seen
beats solid on their frame, inferred beats hatched across the two frames that bracket them,
a marker at each asked-for frame — the beats in words (hover one to light its frames), the
events, the camera and what it was unsure of.

**The rank believes it** (`roughcut/events.py`): a jump / fall / crash claim that a deep span
covers is `confirmed` when the deep look found the same family there and `contradicted`
otherwise (camera roll, nothing, or a fall under a jump claim); a deep event no earlier claim
made ranks with its own weight `DEEP` 1.3 (argued in the code: above an unaudited guess,
below two looks agreeing). The Ask's inventory carries the deep beats, marked seen or
inferred, in place of the coarse and close lines for those seconds.

**API.** `GET /api/coverage/{clip}` — `duration`, `layers.{heard, coarse, close, deep,
motion}` (spans, sample times, interval, width, role, `model` or null, `frames_recorded`),
`moments` (each claim with its `status`) and `unusable`; free, from the files.
`GET /api/coverage` — the same, compact, for every clip (the open screen's cards).
`POST /api/deep {clip, start, end, dry_run?, force?}` — `dry_run` returns the plan and price
(`frames`, `frames_plan`, `projected_usd`, `max_usd` with the follow-up, `eta_s`, `cached`,
`capped`); without it starts the job (`job`), or answers `cached` for a span already read;
409 while one runs or past the cap, 400 with no proxy. `POST /api/deep/quote {spans: [...]}`
prices many at once. `GET /api/deep/{clip}` — the clip's deep looks whole (beats, events,
camera, frames, model, cost) and `frames_url`; `/media/deep/<stem>/<frame>.jpg` serves the
keyframes, cut under `--work/deep/<bin>/<stem>/`. Stored as `<stem>.deep.json` beside the
visual sidecars. **Provisional:** the cap, floor, width and the price (no deep call has been
measured; ≈ $0.51 for 24 frames on Opus 5.5, ≤ $1.07 with the follow-up).

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

**The Ask reaches colour (INTAKE M10, I10.5).** A revision prompt also carries a short
*Colour* section: the film's `colour` block, the looks library by name and description, the
override vocabulary with its limits, and one line of measured numbers per shot in the cut (the
white surface's L\* and cast, clipped %, the balance it resolves to now). A note about the
picture — *warmer*, *less blue on the lift shot*, *make it pop*, *match the lift shot to the
summit* — answers with a `colour` **patch** (only what changes; per-shot overrides keyed by
the shot's id), validated against the real library: an invented look or a number outside the
vocabulary is a failed plan and gets the same one re-ask. A colour-only note may leave
`segments` out, and the proposal says *the cut unchanged — only the colour changes*. The panel
shows the grade in words (`look: filmic at 0.6`, `shot 3 · CLIP_07: warmer, brighter`);
Accept merges the patch over the board's block and saves it with the segments in one write
(colour is not on the undo stack, as in the inspector); Discard drops both. The ✎ shot ask
offers the same clause for that shot alone and rejects a patch that touches the film or
another shot. A proposal's shots now carry the ids of the shots they continue (same clip,
half the shorter range overlapping), so an Accept no longer re-keys every shot and strands
their colour overrides.

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

Keys (`?` shows them all): `j`/`k` move · `space` play / pause the cut from here · `enter` play this shot only ·
`[` `]` trim in · `{` `}` trim out · `x` remove · `u` undo · hold `shift` for 1s steps · in the bin,
`enter` adds the selected keep at the playhead and `space` plays it there.

## Tests

```
# API layer — no browser needed, runs anywhere
uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy pytest app/tests -q

# plus the browser layer (skipped automatically if playwright is absent)
uv run --with playwright playwright install chromium
bash app/tests/install_browser_deps.sh     # no-sudo system libs, one-off
source app/tests/browser_env.sh
uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy --with playwright \
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
- **Orientation is still not proposed** (junk is — see *Junk* above). Whether the bin's
  rotation metadata should be honoured is decided outside the app (`--orient` at scaffold
  time; proxies default to `auto`). The junk thresholds are one bin's numbers, untested on a
  second raw bin.
- **Music mode (P2.6) is not built.** A bed under the cut is (the Music panel); cutting *to* a
  track is not. The slot-driven contract it implies ("fill these N slots of these lengths") is a
  different selection problem, filed in FUTURE_PHASES. Nothing aligns a cut to a beat yet — a
  cut that lands on one does so by luck.
- **A cut is the whole EDL, and the pass's verdicts live in it.** Keeps, rejects and notes made
  on the floor while on one cut are that cut's; another cut of the same bin does not see them.
  Right for "try different things", and a seam if the floor is ever worked on across cuts — the
  fix then is to move `selects` / `floor` / `look` into a per-bin file, not to merge on switch.
- `complete_many` exists per SPEC §6.1 but nothing batches yet; the CLI backend would just loop.
