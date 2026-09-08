# Intake build — tracker

**Start here for the intake workstream.** This file is the source of truth for building the
intake design ("The Cutting Room Floor"): what is decided, what is done, what is in flight,
and where to pick up after an interruption. HANDOFF.md points here; nothing about this
workstream needs to live anywhere else.

The design itself is in the repo, so it survives any hosting:

- [docs/design/cutting-room-floor.html](design/cutting-room-floor.html) — the intake design,
  final proposal (also published: https://claude.ai/code/artifact/b02ae6fe-3c57-4ebf-8615-51bead672585)
- [docs/design/reframing-the-cut-board.html](design/reframing-the-cut-board.html) — the
  editing-room review; Option B is the room the intake feeds
  (https://claude.ai/code/artifact/4a250a90-e6b0-4d55-b886-453603882400)

## How to use this file

- One item = one commit (CLAUDE.md's rule). An item is checked only on **fresh verification
  output** — the suite green, or a live check on a real bin, named in the item.
- Status marks: `[ ]` todo · `[~]` in progress (say the branch) · `[x]` done (say the commit).
- Before ending a session, update **Where we are** and the **Lanes in flight** table. A
  session that is interrupted mid-item leaves the item `[~]` with the branch name; the next
  session merges or discards it — never wonders.
- Scope discovered while building goes under **Discovered**, not into a running item.

## Decisions (do not re-litigate)

Karl, 2026-09-07, from the review round:

1. **Keep = what you actually watched**, snapped outward to sentence ends; `}` extends to the
   next line. Never the machine's whole window blind.
2. **Keys: P / X / U** (pick / reject / later), `1` hero, J-K-L intact (K pauses), Caps Lock
   auto-advance, `⌘Z` undo with trim and note.
3. **Index runs unattended and resumable**, in a priority order, and **releases clips whole**
   (proxy + look + close look + picks all present) while the rest continues. The goal is NOT
   "edit after four minutes"; it is unblocking first steps with full fidelity.
4. **Footage is cut once at home, but clips may be added at any time**, several at once. Added
   footage is the same path as resume: index what isn't done.

Defaults chosen in the design, overridable without redesign: workers and budget cap in
settings; themes are a brief and a filter, never a coverage score; hero = must appear, model
may trim inside; order modes at launch = rank and by-clip; rounds of 40; priority score =
speech candidates + telemetry peaks + theme hits, capture order one click away; witnesses have
three states (claimed / audited / contradicted) and the telemetry witness shows numbers only
until R11.

## Where we are

_2026-09-07, end of session 8._ All five lanes are merged on `main`: M0, M1 (+ server wiring),
I2.1–I2.3 + I2.5 (the pass is live at `/floor`), I3.1 (journal module), M4 (dictation), I6.1
(R11). The floor was verified live on Killington (I2.6). **Open:** I2.4 (compare takes needs
take clusters in `picks.py` first), I3.2/I3.3 (wire the journal into the server — the lead's
next item; `journal.released_clips()` should replace `released_clips()`), M5 (the open screen),
I6.2 (telemetry witness with R11's rule: freefall `< 0.5 g ≥ 0.25 s` as a capped corroboration
term, impacts and tilt as numbers). **Karl used the floor and reported (2026-09-07, bedtime):
trimming must be click-and-drag, the green band must not grow while playing, the strips need
to explain themselves. That is I2.7 and it is the next item — before I3.2.**

## Milestones

### M0 · Foundations (shared contract the lanes build against)

- [x] I0.1 Tracker, design docs in repo, HANDOFF pointer — commit `2810e61`
- [x] I0.2 `roughcut/picks.py` — picks from candidates + events (+ telemetry/themes hooks),
      merged windows, witnesses with three states, anchor + preview rule, verdict re-attach by
      overlap, rounds and order modes. DoD: `test_picks.py` green. — `2810e61` (8 tests)
- [x] I0.3 `roughcut/selects.py` — `selects` + `floor` in the EDL: apply verdict (merge
      overlapping keeps), note, used_in, summary. DoD: `test_floor.py` API tests green. — `2810e61`
- [x] I0.4 Floor API in `app/server.py`: `GET /api/picks`, `POST /api/floor/verdict`,
      `POST /api/floor/note`, `PUT /api/floor/position`, `GET/PUT /api/selects`,
      `POST /api/dictate` (501 until the dictation lane lands), `GET /floor`. DoD: suite green.
      — `2810e61` (suite 265 passed)

### M1 · The bin in the EDL (lane `agent/bin`)

- [x] I1.1 `revise.originate` reads the bin: "## The editor's selects" — heroes fixed, keep
      ranges as bounds the model may trim inside, notes quoted per moment; the rest of the
      inventory for connective tissue only. Validation: a proposal that drops a hero must name it
      in `notes`. DoD: prompt test + validation test. — commit `a88763a` (`agent/bin`);
      server passes `selects=` through in the M1 wiring commit (test: an ask carries the bin
      into the prompt).
- [x] I1.2 `used_in` maintained on accept (server: when segments are saved, recompute) and
      surfaced in `/api/selects`. DoD: API test. — `selects.sync_timeline` on every save
      since `2810e61`; the API path is tested in `9b8846a`'s I1.3 tests.
- [x] I1.3 Hand-added shots become keeps (a saved segment with no overlapping select creates
      one, `source: "hand"`). DoD: API test. — commit `9b8846a` (`agent/bin`).
- [x] I1.4 Relink: a select whose clip is missing is flagged `missing: true`, matched back by
      duration + first-MB hash when a file reappears under another name. DoD: unit test.
      — commit `437a4c9` (`agent/bin`: `selects.relink` + `clip_duration` on creation, duration match only —
      the first-MB hash needs file reads and belongs with the journal's probe stage, M3;
      a `missing` select now round-trips through `PUT /api/selects` untouched). Server side
      wired in the M1 wiring commit: `relink` on every `GET /api/selects` (written back only
      when something changed), `clip_duration` recorded on floor keeps.

### M2 · The pass (lane `agent/floor`)

- [x] I2.1 `/floor` page: HUD, picture with margins (context left, witnesses right), reason
      line with conflicts, whole-clip tape (picks, waveform placeholder, telemetry trace when
      present), zoomed strip with words / sentence bars, kept-range = watched, snapping via the
      sidecar word spans, stamps, key line. DoD: browser tests (`test_floor_ui.py`) — branch
      `agent/floor`, commit `bed7a9c` (suite: 281 passed).
- [x] I2.2 Keys per decision 2, including `⇧X` (reject the rest of this clip), hold-space,
      `[ ] { }` snapping, `← →` frame step at the active edge, `E` evidence drawer, `.` more —
      same commit. (`⇧X` is untested on the synthetic bin, which has one pick per clip.)
- [x] I2.3 Rounds of 40 with frozen queue, closing card (counts, "if strung out", arrivals since
      round start; free default ↵ = play the bin; Assemble on `A` with the price). Resume from
      `floor.position` — same commit. The unlooked-stretch warning is omitted this round.
- [x] **I2.7 — Trim by drag, and make the strips legible** (Karl's first report on the
      pass, 2026-09-07, bedtime): *"It would be easier if I could adjust the start and end of
      the clips by clicking and dragging rather than adjusting with keys, like the arrow keys,
      or watching the green bar … increase as I play. It's fairly nonintuitive and needs to be
      simpler … I like the transcript. And I like the idea of the green bar, which should show
      the part that will be turned into a clip. But it's not clear how it fits into the bigger
      picture, or what the markers are above in the whole clip."*
      The read: trimming is key-only and the band grows while he watches, so the tool looks
      like it is deciding; the tape's markers and the zoom strip's relation to the tape are
      unexplained; too much is on screen. Four moves, in this order:
      1. **Direct manipulation is the primary path.** The green band is the clip: drag either
         edge to trim (snap to sentence ends / word starts with the tick lighting up, picture
         parked on the edge frame, a time readout on the handle), drag the middle to slide the
         range, click on either strip to seek, click a tape marker to jump to that pick. Keys
         stay as accelerators.
      2. **Playing never silently changes the selection.** The band = the preview when the
         pick loads (decision 1's intent — never keep the machine's whole window blind — still
         holds); extending is explicit: drag the end or `}`; hold-space only watches.
         *Decision for Karl, one line: should hold-space also extend the band visibly (a dashed
         follow that commits on release), or never? Recommendation: never — simplest.*
      3. **Legible strips.** A time ruler on the tape; this pick as a bright bracket; the other
         picks in the clip as small markers with a legend in the margin (this · picked · later ·
         undecided), hover for the reason; a **lens** drawn on the tape showing exactly the
         seconds the zoomed strip shows, so the two tiers visibly relate.
      4. **Less on screen.** The key line collapses to the six keys that matter (P X U · space ·
         [ ] { } · V · ?); the full map stays behind `?`. The transcript stays as it is.
      DoD: browser tests — pointer down/move/up on a handle changes the kept range on disk and
      snaps; dragging the middle slides it; clicking a marker jumps picks; the lens tracks the
      playhead; the band does not change during plain playback. Then a second look by Karl.
      — **done on `agent/floor2`**, one commit per move: `a822b6b` (drag handles with a magnet
      to the ticks, the band slides, click either strip to seek, click a mark to jump — marks
      in this round's queue only, decided or not; earlier rounds' marks say so and stay put),
      `4036b6a` (the band is the preview until a hand moves it; hold-space never extends it,
      the recommendation taken; the two tests that encoded the old rule rewritten), `46ee3d4`
      (ruler, bracket, legend, lens, labels, the key line down to six things).
      `test_floor_ui.py` 20 passed · suite 327 passed, 1 skipped. **Next human look:** on
      Killington, load a pick whose preview starts mid-sentence and confirm the band shows the
      snapped preview before any hand touches it and stays put while space is held; drag a
      handle and watch the tick light; check the ruler and lens read right on a five-minute
      tape, and that a mark from an earlier round says why it will not jump.
- [ ] I2.4 Compare takes (Survey view) for clustered picks; batch reject via filter.
- [x] I2.5 Dictation UI: hold `V` → MediaRecorder → `POST /api/dictate` → note attached; clip
      audio ducked while held; `N` edits — same commit, against the 501 stub (falls back to
      `N`); the success path is tested with the recogniser's answer scripted in the page.
- [x] I2.6 Live verification on Killington (2026-09-07, integrated `main` `782f24a`, board
      restarted on it): `/api/picks` → **92 picks over all 12 clips, 3 rounds**, kinds
      crash 3 · jump 17 · fall 6 · faces 8 · action 8 · reaction 1 · speech 45; 120 heard
      witnesses, 53 seen-claimed, 7 seen-contradicted, **0 audited** — correct, the bin's
      `events.json` holds no `confirmed` event (121 unseen · 8 unsupported · 5 contradicted);
      7 picks carry a `conflict`. The river fall (CLIP_01 84–108) ranks 6th with a heard and a
      seen witness. `/floor` rendered round 1 · pick 1 of 40 with reason, seals, zero buttons in
      the flow; CLIP_06's proxy opened at the anchor (`#t=212`), played its 6 s preview and
      paused. **P** stamped PICKED and wrote `CLIP_06 212.0–219.08` to `selects` — the watched
      extent, `clip_duration` 319.34 recorded, both witnesses attached; HUD "1 moment · if strung
      out 0:07.1". **Ctrl+Z** removed it; Karl's EDL left as found (21 shots). Not exercised
      live: dictation with a real voice (needs a human at a mic), `⇧X`, the closing card.

### M3 · The journal (lane `agent/journal`, pure module first)

- [x] I3.1 `roughcut/journal.py`: per-clip stages (probe, telemetry, asr, proxy, look, close,
      picks) with done/running/failed/queued, attempts, cost; reconcile with sidecars on disk
      (files are truth, the journal is the plan); resume = re-queue what isn't done; priority
      score from the free stages; `released(clip)` = every stage done. DoD: unit tests incl. a
      simulated crash mid-stage and an added clip. — commit `f7555a2` on `agent/journal`
      (`test_journal.py` 18 passed; suite 283 passed)
- [x] I3.2 Wire into the server: one `POST /api/index` (start/resume) replacing the two
      buttons' orchestration; per-clip release feeding `/api/picks`'s `released`; failure states
      (backoff on rate limit, park after 3 failures, cap pauses priced stages). DoD: API tests
      with stubbed tools, then a live unattended run on Killington with a kill mid-run.
      — the I3.2 commit (see log; `test_index.py` 5 passed). The two old buttons stay for
      now; the open screen (M5) retires them. Live kill-and-resume: see the verification log.
- [x] I3.3 Added footage: new stems → free stages → queued → delivered as a round; relink by
      hash. DoD: API test adding a clip mid-run. — same commit (`CLIP_D` copied in after a
      run is indexed and released; the floor picks it up at the next round boundary). The
      first-MB hash relink is deferred to the probe stage — see Discovered.

### M4 · Dictation (lane `agent/dictate`)

- [x] I4.1 `roughcut/dictate.py` + `research/tools/dictate.py`: webm/opus or wav in → ffmpeg →
      faster-whisper (same model family as the audio pass, GPU when present) → text; `names`
      from the brief seed the prompt. DoD: unit test with a synthetic tone + a stubbed model;
      one live check that a spoken sentence comes back. — commit `ac7dca2` (`test_dictate.py`
      9 passed + the live test on a 2 s tone: `small` on cuda/float16, 1.3 s warm in the tool,
      4.7 s end to end; a spoken sentence still needs a human at a mic — I2.6 covers it).
- [x] I4.2 `POST /api/dictate` real (≤ 30 s, 413 above), returns `{text, latency_ms}`. —
      commit `ac7dca2` via the pre-written endpoint (raw body, not multipart, by design: the
      floor posts a Blob). The 413 is on bytes (6 MB) and on decoded length: `dictate.TooLong`
      answers 413 since the dictate merge (`50899d0`).

### M5 · The open screen (after M2/M3)

- [x] I5.1 Contact sheet of the folder with free flags (junk band, side-data, telemetry
      present), sessions by the 4 h rule. — commit `5036e44` (`agent/open`): `/open` on
      `GET /api/clips` — bin line, one grid per session in capture order, a card per clip
      (first frame or a placeholder, length, `listened` / `not yet`, `telemetry` /
      `no telemetry`, `looked`, `released`), the journal's word on the picture once the bin
      has one, a legend. The junk band and the rotation side-data are not on the wire yet
      (`/api/clips` carries neither) — flags shown are the free facts it does carry.
      `test_open_ui.py` 2 passed; suite 332 passed, 1 skipped.
- [~] I5.2 Themes proposed from transcripts (one judge-role call), chips + dictation. — the
      data side is done (the I5.2 commit, see log): `roughcut/themes.py`,
      `POST /api/themes/propose` (job, priced), `GET/PUT /api/themes` (`themes` + `names` in the
      EDL, read by picks and the priority score). **Open:** the chips + dictation UI on the open
      screen — a follow-up for the open lane once I5.1/I5.3 land.
- [x] I5.3 Granularity slider re-pricing live; workers + cap in settings; "Index" starts the
      journal. — commit `a147cbd` (`agent/open`): the right column — price before the
      button (`visual.projected_usd`), budget line, order toggle, **Index the footage** →
      `POST /api/index` (409 said, never doubled), the paused notice with the reason and
      **Resume priced stages**, progress from the journal polled every 2 s (bar, released
      n of N, cost, ETA, log) and the Fig. 2 per-clip table, **Open the pass →** on the
      first released clip. **The granularity slider is deferred** until the visual pass's
      sample interval is plumbed through the index — the page says so in a hint, nothing is
      faked; workers + cap in settings are not built (the cap is shown, from config). The two
      old buttons on `/` are still there — retiring them is the lead's (`app/static/index.html`,
      `app.js`). `test_open_ui.py` 6 passed; suite 336 passed, 1 skipped.
- [ ] I5.4 Project picker (one bin per launch today) — the "smaller, whenever" item, lands here.

### M6 · Telemetry (lane `agent/telemetry`, research only until R11 says otherwise)

Karl's rules (2026-09-07), moved here from HANDOFF roadmap item 8: **optional** (not always
present — the pipeline must run identically without it); **when present, weight it with the
visual and audio passes** as a corroboration term in the existing `events.py` rank, never a
detector of its own; **be very careful not to over-index — it could be noisy or bad** — so no
weight until measured, and until then numbers only, never event names.

- [x] I6.1 R11 study: confirm GPMF streams in the Killington files; extract ACCL/GYRO/GPS with a
      standalone tool (`research/tools/telemetry.py`, PEP 723); compute freefall runs, impact
      peaks, speed; score against the nine adjudicated events AND random windows; report in
      `research/R11-telemetry.md`. No pipeline integration in this item. — commit `f806e13`:
      GPMF present 12/12 (HERO9, 198.5 Hz IMU, 18 Hz GPS); the 0.3 g freefall rule never fires
      and 3 g impacts fire 6/min, but a < 0.5 g / ≥ 0.25 s freefall run is clean (10 of 10 by
      eye were real motion) and impact peaks are mostly hands on the camera; orientation matches
      the 9 labelled mounts and refuted every "camera inverted" reading (body roll ≤ 30°); one
      6.7 g peak was a fall R10 had missed. Recommendation: freefall may carry a capped
      corroboration weight; impacts and tilt stay numbers on the witness.
- [x] I6.2 Only if R11 lands: `felt` witness in picks (numbers only), corroboration term in
      `events.py` with a fitted weight, gyro orientation as a check on `seen` flip claims.
      — the I6.2 commit (see log): telemetry as an index stage (skips without a `gpmd`
      stream), freefall runs ≥ 0.25 s under 0.5 g and impacts > 5 g as `felt` witnesses with
      numbers only, freefall as the one shape that may corroborate (the picks bonus, capped
      like R10's motion term), `telemetry_peaks` counted on R11's floors. **Not done, on
      purpose:** a weight inside `events.py` (R11 said "capped at the motion track's" and the
      picks bonus already is that; fitting more needs a labelled set — R11 names the 24
      freefall runs as the place to start one) and orientation on the `seen` witness as a
      number (needs the events file to carry per-window tilt; a follow-up once the 9 unlooked
      Killington clips have their close look).

## Lanes in flight

| lane | branch / worktree | scope | state |
|---|---|---|---|
| bin | `agent/bin` · `../roughcut-wt/bin` | M1 | **merged** `2d7182c` + lead wiring; worktree can be removed |
| floor | `agent/floor` · `../roughcut-wt/floor` | M2.1–2.5 | **merged** `782f24a`; worktree can be removed |
| journal | `agent/journal` · `../roughcut-wt/journal` | M3.1 | **merged** `53a2578`; worktree can be removed |
| dictate | `agent/dictate` · `../roughcut-wt/dictate` | M4 | **merged** `50899d0`; worktree can be removed |
| telemetry | `agent/telemetry` · `../roughcut-wt/telemetry` | M6.1 | **merged** `394556f`; worktree removed |
| floor2 | `agent/floor2` · `../roughcut-wt/floor2` | I2.7 | running 2026-09-08 |

Lanes touch disjoint files by design: `bin` → `revise.py`, `selects.py`, tests; `floor` →
`app/static/floor.*`, `test_floor_ui.py`; `journal` → `journal.py`, `test_journal.py`;
`dictate` → `dictate.py`, `research/tools/dictate.py`, tests; `telemetry` → `research/`.
`app/server.py` is the lead's file: endpoints are pre-written; lanes fill the modules behind
them. Every lane adds its own CHANGELOG bullet; integration keeps all of them.

## Discovered

- **The Killington events file has no `confirmed` event at all** (121 unseen · 8 unsupported ·
  5 contradicted; the 7 close-look moments never agreed with a hot coarse claim). So the floor
  shows no `audited` seal on this bin — honest, and a reminder that roadmap item 1 (audit what
  the sheets claim) is still the biggest lever on the visual side.
- **GoPro's GPMF carries its own 10 Hz wind meter (`WNDM`), a wet-mic flag and an audio level**
  (telemetry lane, R11). Relevant to R8/R9's never-validated wind detector; not used yet.
- **R11 corrected an R10 label:** CLIP_11 144.3 s is a real fall (6.7 g, skis against the sky at
  145 s) that R10 had called "the horizon rolls, the rider does not". Recorded in R11.
- **The design's telemetry thresholds were wrong both ways** (freefall `< 0.3 g` never fires;
  `> 3 g` fires 6×/min). R11's rule replaces them; the journal's `telemetry_peaks` input should
  count freefall runs and `> 5 g` peaks, not `> 3 g`.
- **A `picks` sidecar does not exist** — picks are derived per read. I3.2 must either write one
  per clip (`<stem>.picks.json`) or treat `picks` as done when `look`/`close` are.
- **A first-MB content hash for relink** needs file reads; it belongs with the journal's probe
  stage (I3.2), not in `selects.py`.
- **A zombie ask** (a thread dying with the job still `running`) disabled every board's Ask
  button; `_ask_job` now fails loudly on any exception. Found by two UI tests failing together.
- **Killington is not "fully indexed" by decision 3's definition**: the old close look ran on
  3 clips, so 9 clips still have `close` queued in the journal (~27 model calls, ~$2). The
  lead paused the priced stages after the live resume test rather than spend that without
  Karl. **To finish the bin:** `POST /api/index {"resume_priced": true}` (or the open screen,
  once M5 exists). While paused, the floor releases clips whose free stages are done — picks
  from the words and the coarse look — so nothing disappeared.
- **The old Analyse / Look buttons and `POST /api/index` coexist** until M5 retires the two
  buttons; running both on one bin is safe (files are truth) but pointless.

## Verification log

- 2026-09-07 · foundations `2810e61` · 265 passed
- 2026-09-07 · + journal `53a2578` · 283 passed
- 2026-09-07 · + dictate `50899d0` · 293 (one race in the job-list test fixed, 3/3)
- 2026-09-07 · + bin & wiring `9c7bcb4` · 307 passed, 1 skipped (live dictation)
- 2026-09-07 · + telemetry `394556f` + floor `782f24a` · see the integration commit's body
- 2026-09-07 · live on Killington · I2.6 above
- 2026-09-08 · I3.2 live kill-and-resume on Killington: CLIP_03's proxy moved aside,
  `POST /api/index` started (probes 12/12, reconcile marked every existing sidecar done,
  telemetry skipped, CLIP_02's close look started), the server killed mid-stage; on restart
  the journal read `reconciled: CLIP_02.MP4 close re-queued`, rebuilt CLIP_03's proxy
  (17,019,121 bytes, identical to the original), left the priced stages paused, and the floor
  showed 12 clips / 92 picks again. Cost of the whole exercise: three in-flight sheet calls,
  ~$0.08. `test_index.py` 5 passed; suite 328 passed, 1 skipped.
- 2026-09-08 · I6.2 live on Killington: the index reopened the 12 stale "not integrated"
  telemetry skips and ran the stage (the R11 tool's cache made it seconds, $0); telemetry
  `done` 12/12; `telemetry_peaks` facts 0–22 per clip (CLIP_02's 22 saturates the term at
  its 0.05 cap — the point of the cap); the floor shows **55 felt witnesses** over 90 picks,
  numbers only — CLIP_11's backflip window carries `5.9 g` / `6.7 g`, silent CLIP_05 a
  `0.3 s freefall at 0.03 g` beside 8 g bumps. Priced stages still paused; 12 clips released
  under the paused rule.
