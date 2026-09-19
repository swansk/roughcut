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
5. **A clip's start and end stay adjustable after effects and editing** (Karl, 2026-09-08:
   *"one big value add is always being able to adjust the start / end of clips, even after we
   start applying effects / editing"*). The EDL's segments' `in`/`out` are the only place a
   range lives; every effect is derived from them at render time and never bakes a range in.
   Today's one effect, the music bed (`effects_music`), already works that way — speech
   ducking is recomputed from the segments on every render. Any future effect (speed ramps,
   titles, transitions, overlays) must be keyed to a segment id and expressed in that
   segment's own clip seconds, so a trim never invalidates it; and the board must offer the
   trim at every stage, not only before effects.

Defaults chosen in the design, overridable without redesign: workers and budget cap in
settings; themes are a brief and a filter, never a coverage score; hero = must appear, model
may trim inside; order modes at launch = rank and by-clip; rounds of 40; priority score =
speech candidates + telemetry peaks + theme hits, capture order one click away; witnesses have
three states (claimed / audited / contradicted) and the telemetry witness shows numbers only
until R11.

## Where we are

_2026-09-18, end of session 14 (late)._ **M9, the promoted timeline, is built: I9.0–I9.5 and
I9.7 ticked; I9.6 is Karl's look.** I9.7 answered Karl's *"extend a clip while offsetting the
next instead of cutting into the next … top of the clip extends, bottom cuts in"*: every cut has
an edge column whose top half extends or shortens the shot and moves the rest, and whose bottom
half rolls; ⇧ flips; the half under the pointer lights up with a ghost of the pushed shot and a
one-line hint (`e8818aa`). **M10 (colour) is planned** in this file as the priority after I9.6. **M10 (colour: correct, match, look) is built through I10.4** (2026-09-19, session 16: foundation `d84082d`, lanes `agent/colour-render` `5155662` and `agent/colour-ui` `0e959c0` merged, grey-scene rule `1e7f75c`); I10.5 (the Ask) and I10.6 (Karl's look — the Killington cut is rendered both ways in `roughcut-lab/out/`) remain. Karl's feature 2 (*"improve the timeline, review what features in tools
like Premiere Pro make timeline editing a breeze and add all of these"*) — the review is at
the top of M9; everything on its list is on the board: a real timeline with a ruler, zoom and
scrub (`139e171`); ripple / roll / slip by drag with the magnet to cuts, the playhead,
sentences, words and onsets (`1a33b0e`); JKL, `↑`/`↓`, I/O + insert, C, Q/W, X, ⌘A, ⌘D, the
map (`034551b`); lanes — markers, the music bed with its ducks, the bin's available keeps,
the proposal ghost — and drag-move with a drop line, drops from the bin and Find (`9ff755d`);
stable shot ids and snap points on the server (`c6cdc8d`); the inspector in place of the
card list (`e3a2c60`). Two merge-only collisions fixed (`db94f5e`, `f1411c5`). Suite: **452
passed, 1 skipped**, plus three tests that time out only under full-suite load and pass alone
(`test_0_restarts…`, the two music-lane tests). Board live on `e3a2c60`. **Next: Karl's look
at the timeline (I9.6)** — trim a shot by its edge and watch the snap line, roll a cut, press
`/`-free keys: `J K L`, `↑ ↓`, `C`, `Q`/`W`, drag a keep from the kept tab onto the timeline,
ask for a change and read the ghost lane, and say what feels wrong. After that: his feature 3;
the three small spends still waiting (re-look 11 clips ~$2, close looks ~$2, the themes
Keep/Discard). Deferred from M9, named in the lanes' reports: a `tl.on('mount')` and
`tl.on('render')` event and a `tl.reveal(id)` on the foundation (the lanes worked around
them); a pre-ask price for "Cut from the bin".

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
- [x] **I2.8 — Karl's second look at the pass (2026-09-08).** Three sentences: *"the audio
      note doesn't work"*; *"it is unclear where the subclip is within the whole clip
      timeline on the pass"*; *"when I pick, reject, or later a clip, it should move on to
      the next one. Right now, I'm not sure how you move on"*. One commit each, on
      `agent/takes`:
      1. `b58e2c8` — the audio note. The server was fine (a real recording came back in
         4 s); the browser flow was not: Chrome's first-time permission prompt let V come
         up before the stream arrived and the stream was dropped in silence, and a refused
         microphone fell into the typed editor. Now the permission is learned at boot and
         said in the hint (prompt / blocked / absent, the V key coloured by it), a late
         stream is kept and announced, one stream stays open for the page, a hold under
         0.4 s is dropped and said, the failures are told apart, and only a 501 — the
         recogniser genuinely absent — opens the typed editor.
      2. `1a2815c` — where the subclip is. The kept range is a solid green band on the tape
         inside the bracket, live under a drag; a bridge between the strips fans the lens
         out to the closer strip and carries the playhead across; THIS PICK says
         `2:20 → 2:27 of 5:18 · 44 % in`.
      3. `911b8b7` — a verdict moves on, always. Caps Lock, the HUD pill and `F.auto` are
         gone; ↵ skips for now, ⌫ goes back, a revisited pick shows its stamp and can be
         re-decided.
      `test_floor_ui.py` 24 passed (three new, five rewritten) · suite 352 passed, 1 skipped.
      **Next human look:** on Killington, hold V once in Chrome and read the hint before
      and after the prompt; find CLIP_11's backflip on the tape without reading a number;
      P three picks in a row and watch the pass carry you.
- [x] I2.4 Compare takes (Survey view) for clustered picks; batch reject via filter.
      — two commits on `agent/takes`: `7719b72` take clusters in `picks.py` (same clip, same
      kind, no overlap, at most `TAKE_GAP_S` = 90 s apart — the hike back up to a kicker is
      one to two minutes, a fall and its replay are seconds; speech clusters only on a
      shared theme tag; `take: {id, n, of, others}` on every pick in a cluster, `null`
      elsewhere; computed after verdicts re-attach, rank and order untouched) and
      `be992ea` the Survey view on the floor (THIS PICK says `take n of N · T to compare`;
      `T` lays the takes out in time order — still, range, kind, strongest witness, felt
      numbers, verdict, `★` on this pick, an earlier round's greyed with the reason;
      `← →` choose, `↵` or a click goes there, `P` keeps one and rejects the cluster's other
      undecided takes one POST each with `why: other take of <cluster>` in one undo entry,
      `X` rejects the chosen take only, `Esc` closes; `T` row in the `?` map). The
      batch-reject-via-filter half was built later, in session 12 — see I8.2 under M8
      (`262e301` on `agent/filter`: `/` opens the filter line, `⇧X` / `⇧U` reject / later
      every undecided pick that matches, one undo entry).
      `test_picks.py` 12 passed (3 new) · `test_floor_ui.py` 25 passed (1 new) · suite 356
      passed, 1 skipped. **Gap for the bin lane:** `selects.apply_verdict` keeps only `note`
      on a reject/later, so the reject's reason is on the wire but not in the EDL — one
      line in `selects.py` (`"why": str(why)[:300]` in the verdict dict) closes it.
      **Next human look:** on Killington, find a clip with two jumps under 90 s apart
      (CLIP_06 or CLIP_11), read `take n of N` in the panel, press T, keep the one that
      landed with P, ⌘Z, and check both verdicts come back.
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
      — the I3.2 commit (see log; `test_index.py` 5 passed). The two old buttons were
      retired by lane `agent/board` (merge `6c4d249`). Live kill-and-resume: see the verification log.
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
- [x] I5.2 Themes proposed from transcripts (one judge-role call), chips + dictation. — the
      data side `fc26979`: `roughcut/themes.py`, `POST /api/themes/propose` (job, priced),
      `GET/PUT /api/themes` (`themes` + `names` in the EDL, read by picks and the priority
      score). The screen `cce429c` (`agent/open2`): on `/open`, at the top of the right column
      as Fig. 1 — the story field with hold-to-speak (the mic, or `V` held in the field; a tap
      types), the price before **Propose**, "listening…" while the job runs, chips with clip
      counts (line + why on hover) and names as toggles, **+ add**, **Keep** = one PUT of
      exactly the ticked ones, **Discard** writes nothing; kept themes rest as chips with
      **change** and **propose again ~$X**; a bin not yet heard says so. `test_open_ui.py`
      12 passed; suite 354 passed, 1 skipped. Not built: the mic only learns the recogniser is
      absent from a 501 (no cheap endpoint on `/open` carries `dictate.available()`).
- [x] I5.3 Granularity slider re-pricing live; workers + cap in settings; "Index" starts the
      journal. — commit `a147cbd` (`agent/open`): the right column — price before the
      button (`visual.projected_usd`), budget line, order toggle, **Index the footage** →
      `POST /api/index` (409 said, never doubled), the paused notice with the reason and
      **Resume priced stages**, progress from the journal polled every 2 s (bar, released
      n of N, cost, ETA, log) and the Fig. 2 per-clip table, **Open the pass →** on the
      first released clip. **The slider** landed later, on `agent/open3` (`621925a`): four
      stops 4 · 3 · 2 · 1 s each said in words, the price line and the button re-pricing
      from `by_interval` as the thumb moves (no request per move), `interval_s` sent with
      **Index**, the project's word shown after a run, disabled with a reason once every
      clip has been looked at (`test_open_ui.py` 13 passed; suite 360 passed, 1 skipped);
      workers + cap in settings landed last, as I8.3 (the server's `GET/PUT /api/settings`
      and the drawer behind the gear on `/open`). The two
      old buttons on `/` were retired by lane `agent/board` (`6c4d249`). `test_open_ui.py` 6
      passed; suite 336 passed, 1 skipped.
- [x] I5.4 Project picker (one bin per launch today) — the "smaller, whenever" item, lands here.
      — **server side** (the lead's I5.3/I5.4 commit, `d889db1`): `GET /api/projects`
      (known bins from the `--work/projects.json` registry + folders of video next door, with
      facts), `POST /api/projects/open {footage}` (re-points through `configure()`, 409 while
      a job runs). The slider's server side landed in the same commit: `look.interval_s` in
      the EDL, `by_interval` prices on `/api/status`, `POST /api/index {interval_s}`.
      **The screen** on `agent/open3` (`aaf43df`): the bin's name in the header is the control (click or
      `O`) → a panel of the bins with `N clips` and flags (`cut · N shots` · `journal` · `new`
      · `missing`), a row or a typed path → `POST /api/projects/open` → the page reloads
      everything for the new bin and toasts `opened <name>`; 409/400 said, panel stays;
      `Esc` closes. No browsing dialog (a page has none — the hint says so).
      `test_open_ui.py` 15 passed; suite 362 passed, 1 skipped.

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

### M7 · Karl's third report (2026-09-08)

*"Evaluate the prompt for image evaluation, several clips, e.g. CLIP_04 say things that are
not true like 'person in dark clothing appears to be inverted or airborne'. CLIP_07 too.
Identify what is wrong and fix it. Indicate how much of the clip was indexed by keyframe —
helps to debug / determine why it is good. The indexed keyframes should be referenced in the
why and timestamps should be jumpable. Add a restart from beginning of clip in the pass."*
(The start/end-after-effects point is Decision 5.)

- [x] I7.1 **The sheet prompt lies; find out why and fix it.** Adjudicated against the rebuilt
      sheets (`contact_sheet.py`, same params): CLIP_04 0:04 "person in dark clothing appears
      to be inverted or airborne" is **the wearer's glove over the lens**; CLIP_04 4:12 "person
      performs aerial flip or backflip; captured inverted mid-air" is **a ski binding across
      the lens**; CLIP_04 4:24 "person tumbling or falling" is **a glove**; CLIP_07 2:48–3:16
      "person airborne in sustained jump sequence across 8 consecutive frames" is **two people
      standing on a slope shot from below**, while the real jump at 3:36 was called "standing
      or walking". Also sub-second moments (`1.0–1.1`) at 4 s sampling. Four causes: the prompt
      never said the camera is a helmet POV whose wearer's gloves, poles, ski tips and bindings
      fill the frame; it primed for events and accepted hedged wording as `notable`; it asked
      for no frame references, so nothing pinned a claim; it stated no physical bound (nothing
      is airborne for 28 s). Fix: `visual_pass.py` `PROMPT_VERSION` 2 — SYSTEM names the
      camera and the wearer's gear; the prompts list the frame timestamps and require
      `frames` per moment, `confidence`, plain wording, the one-or-two-frames rule for an
      event, `pov-gear` as a kind; `validate()` snaps edges to sampled frames, and demotes
      to `notable: false` (reason in `demoted`) any notable moment that is hedged, not high
      confidence, scenery/junk/pov-gear, an event without a frame, or an event spanning more
      than two frames. The sidecar records `frames_sampled` and the prompt version.
      `test_visual_pass.py` 7 tests. **Live check on CLIP_04 with the new prompt:** see the
      verification log. **Open:** the other 11 Killington clips still carry version-1
      sidecars; a re-look is ~22 sheets ≈ $2 (`POST /api/visual {"force": true}`, or delete
      the `.visual.json` files and Index again) — Karl's call.
- [x] **I7.2 — what was looked at, and jumpable frames.** Karl: *"Indicate how much of the
      clip was indexed by keyframe — helps to debug / determine why it is good. The indexed
      keyframes should be referenced in the why and timestamps should be jumpable."* — commit
      `df0e2d2` (lane `agent/floor4`): on the WHOLE CLIP tape a tick under the ruler at every
      frame the look pass read (`/api/picks` `looked[clip].frames`), the ones a `seen` witness
      of this pick cites brighter and taller, the count in the tape's label (`LOOKED · N frames
      · every 4 s · 3 sheets`, or `not looked at yet`), a legend line only when there is one;
      every `m:ss.s` in WHY, on a witness line and in the evidence drawer is a chip — click
      parks the picture on that frame through `seek` (paused, strip re-centred, whole clip
      opened outside the preview) and lights its tick, hover lights it; a `seen` witness shows
      its `frames` (`· frames 2:20 · 2:24`) and `confidence`; the drawer says `this window: N of
      M frames looked at` (`0 of M — nothing under the claim` / `no frame falls in it` / `not
      looked at yet`). A response without `looked` renders as before. Built against the lead's
      contract by injecting `floor.state.looked` and witness `frames` (the synthetic bin has no
      visual sidecar). Two calls where the brief was silent: the witness line's own leading
      time is a chip too, and a chip clicked inside the drawer closes the drawer so the frame
      shows. `test_floor_ui.py` 26 passed (1 new) · suite 373 passed, 1 skipped.
- [x] **I7.3 — restart from the beginning of the clip.** Karl: *"add a restart from beginning
      of clip in the pass /floor."* — commit `453e43c`: `0` (and `Home`), from anywhere on the
      pass with any overlay open, plays the whole clip from 0 with `F.whole` set (the `.` O
      path); `⇧0` replays the band — from the kept range's start, stopping at its end via a new
      `F.until` that `stopAt` honours, cleared by `show`, `undo` and any hand-seek — and never
      moves it. Matched on `e.code` (`Digit0` / `Numpad0`) since ⇧0 arrives as `)`. Both in the
      `?` map; the tape's label mentions `0`; the six-key line is untouched. `test_floor_ui.py`
      27 passed (1 new) · suite 374 passed, 1 skipped. **Next human look:** on Killington,
      open CLIP_11's backflip, read the LOOKED count on the tape and see whether the frames the
      witness cites sit under the jump; click a time in WHY; press `0` mid-clip, then `⇧0`.
- [x] I7.4 Decision 5 recorded (start/end adjustable after effects) and checked against the
      one effect that exists.

### M8 · Session 13 (2026-09-13): the pass feeds the board, and the two deferred halves

Karl, 2026-09-08: *"what should I expect going from the pass to the cut board here? Cut
board looks exactly the same as before."* — the bin was reaching the cut only through the
Ask's prompt. This milestone makes it visible (Option B's Source panel, stage one) and closes
the two halves the tracker had deferred.

- [x] **I8.1 — the bin visibly feeds the cut board.** Karl, 2026-09-08, after using the
      pass: *"what should I expect going from the pass to the cut board here? Cut board looks
      exactly the same as before."* — lane `agent/binboard`, two commits on the board as it
      is (no layout overhaul). `39fbfe4`: **kept**, a third tab under *Add a moment*, first in
      the row and the one the library opens on when the bin has keeps — one row per keep in
      bin order (heroes first, then clip and start): a still at its start, `CLIP · m:ss.s →
      m:ss.s · d s`, `★ HERO`, the pass's `why` (with its `(frames …)`), the note in quotes,
      and either *in the cut · shot N* (click selects the shot) or *+ add to cut* (a shot
      with the keep's range and reason after the selected one, saved straight away so the
      bin learns the use); a `missing` keep says so and cannot be added; the empty state
      points at `/floor`; the Project panel reads `bin · N moments · N heroes · m:ss if
      strung out` from `GET /api/selects` `summary`; the Steps strip's *first cut* says `N
      heroes waiting` while a kept hero is not in the cut; re-read when the tab is shown and
      after every save while it is up. `2d71140`: **Cut from the bin** — under the Ask's
      note, and beside *Ask for a first cut* in the empty state (the panel is hidden there,
      and it is where someone arriving from the pass lands) — `POST /api/ask` with the fixed
      note *"Build the cut from the editor's selects: every hero must appear, use the other
      keeps where they serve the story, and take nothing else unless it is needed to make a
      keep land."* and the current story, then the usual proposal loop; disabled with a hint
      while the bin is empty; the fixed note never becomes the story. Two calls where the
      brief was silent: *in the cut* is decided on the board against the live timeline with
      `used_in`'s own rule (half the shorter range), so a row flips before the autosave
      lands and un-flips when its shot is removed; and the bin button is doubled into the
      empty state because the Ask panel the brief named is hidden exactly when the timeline
      is empty. `test_ui_flow.py` 45 passed (3 new) · suite 394 passed, 1 skipped.
      **Next human look:** on Killington, open the board after the pass — the library should
      open on *kept* with CLIP_04's jump first; press *+ add to cut* on one, then *Cut from
      the bin*.
- [x] **I8.2 — I2.4's other half: batch reject via a filter on the pass.** — commit
      `262e301` on `agent/filter`. `/` opens a filter line under the HUD (the six-key line
      untouched): chips for every kind in this round with its count, four states
      (*undecided* · *claimed only* = a `seen` witness with no audited state and no heard
      witness · *has words* · *has telemetry*), the clips in the round, and a box for a word
      matched case-insensitively against the reason, the conflict, every witness's text and
      the tags (no tokeniser exists on the floor to reuse). Chips of one group OR, groups
      AND. With a filter on: the HUD says `N of M match`, the tape dims the marks outside
      it, `↵` / `⌫` step only through what matches (the queue is never changed — rule 3),
      and the pass parks on the first matching undecided pick when this one falls outside.
      `⇧X` asks once in the HUD (`reject N picks? ⇧X again · Esc`), then one `POST
      /api/floor/verdict` per undecided matching pick in queue order with `why: filtered
      out: <the filter in words>` (e.g. `filtered out: kind jump · claimed only`), one undo
      entry for the lot (`⌘Z` brings every one back and lands on the first), the filter
      clears, the pass moves on to the next undecided pick. `⇧U` likewise with `later`.
      Without a filter `⇧X` keeps its meaning (the rest of this clip) and `⇧U` gains the
      same for `later`. Map rows for `/`, `⇧U`, `⇧X` (both meanings). Decisions where the
      spec was silent: a filter's own move parks rather than plays; `↵` past the last match
      toasts and stays (the card is for a finished round); the first `Esc` drops a pending
      confirm, the next clears the filter; a modifier key on its own never drops the
      confirm; the switcher's `Esc` is honoured first. Tests inject two more picks through
      `floor.state` as the takes lane did (a claimed-only `jump` in CLIP_A, a `fall` with a
      felt number in CLIP_B), the real A and B picks cut short so no two picks in a clip
      share seconds — `apply_verdict` replaces whatever was said about a range's seconds.
      `test_floor_ui.py` 31 passed (4 new) · suite 395 passed, 1 skipped. **Next human
      look:** on Killington, press `/`, click *claimed only*, read the count against the
      round, `↵` through a few, then `⇧X` twice and `⌘Z` once — every one should come back
      and the pass land on the first.
- [x] I8.3 Workers + cap in settings — **server side** the lead's `e28c231` (`GET/PUT
      /api/settings`, `budget_cap()`, workers applied at the next index run). **The drawer**
      on `/open`, `c06f360` (lane `agent/settings`): a gear next to **Index the footage**
      (or `,`) opens it, `Esc` closes — the cap in dollars with the spend beside it and its
      source in a hint (disabled, "the environment wins", when `ROUGHCUT_BUDGET_USD` is set),
      one stepper per stage 1–8 with a one-line hint in words, **Save** = one PUT (the toast
      says what changed, the budget line re-reads, a 400 is said on the drawer), **Reset to
      defaults** fills the form, "changes apply to the next run" while the index runs. Not
      built: nothing on `/floor` or `/` reads settings (the cap shows on `/open` only), and
      the hints are static words, not re-said per count. `test_open_ui.py` 20 passed (3
      new; suite 397 passed, 1 skipped); the module's `budget()` helper pins
      `server.budget_cap` now.
- [x] I8.4 Live check of session 12's cuts (bin · cut switcher, save a copy) on Killington —
      2026-09-13, headless (the in-app pane is unreliable on localhost): the header reads
      `killington-neutral · main`; the panel lists `CUTS of killington-neutral · 2 cuts` —
      `main` and **`killington-future`, a copy Karl had already saved himself** — and `BINS
      3 bins`; `POST /api/cuts/open` on the main cut returns the board to it. The script's
      own Save-copy click did not land a name (the field guess was wrong); Karl's copy is
      the proof of that path. Nothing left behind.

### M9 · The promoted timeline (Option B, stage two) — Karl, 2026-09-18

*"Let's improve the timeline, review what features in tools like Premiere Pro make timeline
editing a breeze and add all of these in the app."*

**The review.** What makes a Premiere / Resolve / Final Cut timeline a breeze is a short list,
and most of it is one idea: the cut is a spatial thing you move with your hands, and every
move snaps to something meaningful. In order of how much each buys an editor:

1. **A real timeline with a global time scale** — a ruler, zoom (`+`/`-`, fit, wheel), scroll,
   the playhead against the whole film, click and drag to scrub. Today's strip is a
   proportional flex row with the playhead computed inside one block.
2. **Trim by dragging edges** — ripple (the film closes up), roll (drag the cut point between
   two shots, one grows as the other shrinks), slip (drag the middle to shift in/out together
   keeping the length). With visible handles, a tooltip of the new time, and the film total
   updating live.
3. **Snapping with a magnet** — to other cut points, the playhead, and Roughcut's own dividend:
   sentence starts and ends and audio onsets. A snap line shows what it took; `S` toggles.
4. **Keyboard-first editing** — JKL shuttle with speed stacking, `↑`/`↓` to the previous/next
   cut, `Home`/`End`, `I`/`O` mark in and out while a full clip plays and `,` inserts it,
   `C` razor at the playhead, `Q`/`W` trim the selected shot's in/out to the playhead, `X`
   ripple delete, `⌘Z`/`⌘⇧Z` undo and redo, `,`/`.` nudge a frame (`⇧` a second).
5. **Drag and drop with a drop line** — move a shot with the film closing up behind it,
   multi-select and move together, drag a keep from the bin tab or a Find result onto the
   timeline at a time.
6. **Lanes** — V1 shots as poster-filled blocks with the strongest witness line, A1 the music
   bed with its ducks drawn under speech, a markers lane (heroes, events, the pass's keeps not
   yet in the cut as faint "available" shapes), and a **proposal ghost lane**: a pending
   proposal drawn under V1 aligned by time, play either, accept.
7. **Stable identity** — every shot has an id that survives reorder, undo, proposals and the
   bin's `used_in`, so nothing is positional any more.
8. **An inspector for the selection** — the card's content (still, transcript, why, ask about
   this shot) for the selected shot only; the card list stops being the editing surface.

Not taken: a source/program pair of monitors (the one monitor stays and plays either the cut
or a clip), nested sequences, multicam, keyframed effects — none of them is what a rough cut
needs. Decision 5 holds throughout: every edit is a change to a segment's `in`/`out`/order,
and effects derive from it at render.

- [x] I9.0 **Server side (the lead, commit: see log — `test_timeline_api.py` 4 tests, suite 407 passed):** stable segment ids persisted through `PUT /api/project`
      and minted once for segments without one; `used_in` by id; `GET /api/snaps/{clip}` —
      sentence starts/ends (with the cut pads), word starts, onset peaks — for the magnet;
      static routes for `timeline.js` / `timeline.css` / `timeline-*.js`.
- [x] I9.1 **Foundation (lane `agent/timeline`, commits `cdcbf46` module + mount, `76bba69`
      tests; `test_timeline_ui.py` 13 tests, with `test_ui_flow.py` 58 passed, suite 421 passed):**
      `app/static/timeline.js` + `timeline.css` — the ruler, zoom, scroll, global playhead +
      scrub, V1 blocks with posters, selection (click, ⇧ range, ⌘ toggle), stable ids on the
      board (`tmp-` until the save re-keys), one undo/**redo** stack for the whole board, and
      the edit API the other lanes build on (`tl.begin`/`commit`, `setRange`, `move`, `split`,
      `remove`, `insert`, `select`, `seek`, `zoomTo`, `fit`, `snapsFor`, `on` — the comment
      block at the top of `timeline.js` is the contract); replaces `paintStrip` in `app.js`;
      the monitor and autosave unchanged. Not in the brief, decided by the lane: a plain click
      on a block also plays from there (the strip's promise, and what keeps `test_ui_flow.py`'s
      monitor test unchanged); a lane-background click clears the selection and no card is
      marked until the next selection.

- [x] I9.3 **Keyboard editing (lane `agent/tl-keys`, commits `6801ae3` transport + navigation +
      marks, `7505826` editing keys; `test_timeline_keys.py` 13 tests, with `test_timeline_ui.py`
      and `test_ui_flow.py` 71 passed):** `app/static/timeline-keys.js` — JKL shuttle (L stacks
      to 8× across the hand-over, J reverses by rAF across cuts, K pauses, K-held combos),
      `↑`/`↓` previous / next cut, `Home`/`End`, `←`/`→` a frame (`⇧` a second) — the playhead
      only, so the trim lane's `,`/`.` stay the edge nudges; `I`/`O` on the clip in Find + `↵`
      inserts the range with the transcript line as why (ticks beside `#pos`); `C` razor,
      `Q`/`W` trim to the playhead, `X`/`⌫`/`Del` ripple delete the selection, `⌘A`, `⌘D`,
      `Esc`; `⌘Z`/`⌘⇧Z` left to the foundation; a Timeline section in the Keys panel rendered
      from the module's one table, `?` brings it up. Decided by the lane: the listener is on
      `window` (capture) because the switcher's `o` is capture on `document`, and `o` stays
      the switcher's until a clip is open in Find; `j`/`k` no longer move the card selection.
- [x] I9.2 **Trims + snapping (lane `agent/tl-trim`, commits `a1f1c58` trims, `4334b35` the
      magnet; `test_timeline_trim.py` 9 tests, with `test_timeline_ui.py` 22 passed, suite
      429 passed):** `app/static/timeline-trim.js` on the foundation's API — edge handles
      ripple-trim, a zone over every cut rolls, ⌥-drag slips; one undo entry per drag, Esc
      cancels, the monitor parks on the dragged edge; `,`/`.` nudge the active edge (⇧ a
      second); the magnet snaps a dragged edge or cut within 8 px to a cut (roll), the
      playhead, a sentence `cut_in`/`cut_out`, a word, an onset, with a labelled snap line,
      ticks inside the dragged block, `S` to toggle (persisted) and ⌘/ctrl to suspend.
      Decided by the lane: the pointer maps to seconds at the zoom of pointerdown (the
      foundation refits until someone zooms); the playhead target is where it stood when
      the edge was taken (the foundation's seek moves it while parking); cut points are a
      roll's targets only.

- [x] I9.4 **Lanes + drag and drop (lane `agent/tl-lanes`, commits `4d52440` the lanes,
      `8d2f69d` drag and drop; `test_timeline_lanes.py` 6 tests, with `test_timeline_ui.py`
      19 passed, suite 427 passed):** `app/static/timeline-lanes.js` + rules appended to
      `timeline.css`, built on the foundation's API and hung inside `#tl` (V1 moves down /
      the view grows through `--tl-above` / `--tl-below`). A1 music with the bed, the fades
      as ramps, a dip per speech region in the cut and the monitor's `bedGainAt` curve (a
      click scrolls the music panel in; it draws, never edits); the markers lane above V1
      (`★` per hero keep in the cut, a tick per ranked event inside a shot, a legend) and a
      bin lane below (the pass's keeps not in the cut as faint `available` outlines after
      the last shot of their clip, else the end); the proposal ghost lane while the diff
      panel is up (unchanged dim, added green, moved with an arrow, removed struck out on
      V1; click a ghost → the monitor plays that range; `play proposal` / `play cut`);
      move by drag on V1 with a drop line snapping to cut points, the whole selection
      together, under 4 px a click; kept rows, Find rows and `available` outlines drag onto
      the timeline as `application/x-roughcut-shot` and insert at the drop line, past the
      end they append. Decided by the lane: outlines that anchor at the same point lay end
      to end; a matched ghost with a different range wears `trimmed`; moved vs unchanged is
      a longest increasing subsequence over the matched shots; the lanes hold still from a
      press on an outline until its drag ends (Chromium ends a drag whose source is rebuilt).
      The foundation lacked a mounted hook, a render event and a play-a-range hook — all
      worked around inside the lane's file (tl.mount / tl.render wrapped, a 500 ms identity
      poll, the monitor's globals driven by name); nothing in the foundation was edited.
- [x] I9.5 **Inspector (lane `agent/inspector`, commits `ac404db` the inspector + the card
      list retired, `f6ed711` tests + docs; `test_ui_flow.py` 5 new tests, the five timeline +
      flow files 91 passed, suite 452 passed + the two music tests passing alone):**
      `#inspector` under the timeline, in the DOM place the card list had — for the anchor
      shot: `SHOT n of N · CLIP · in → out · d s`, the boundary warning and any unusable
      stretch, where it starts in the film, the still at the in-point on the 450 ms settle,
      the transcript lines and what was seen inside the cut, the why as the same
      contenteditable, `↳ polished from …` and `act · …` only when the segment carries them,
      ±0.25 s trims through `tl.begin` / `tl.setRange` / `tl.commit` (one undo entry each),
      ▶ play this shot, ✎ ask about this shot (the scoped ask, unchanged), remove through
      `tl.remove`; a multi-selection shows `N shots selected · d s` with remove for all; no
      selection says *select a shot on the timeline — or press ↑ / ↓* with the cut's totals.
      One element, built per shot and filled in place on `select` / `change` / `paint()`, so
      a scrub or playback never rebuilds it or steals a why being typed. `render()` builds no
      cards; the kept tab's link selects by id through `tl.select`; `scrollSel` brings the
      block into view; the card's drag-to-reorder is gone. Decided by the lane: header times
      in `m:ss.s` with the exact clip seconds in the tooltip; the scoped ask's send button
      reads `Ask` (distinct from the toggle); nothing is invented for provenance — the save
      keeps only clip / in / out / act / why / id, so a proposal's polish shows until the next
      reload. The foundation lacked nothing the inspector needed; one convenience noted only:
      a `tl.reveal(id)` (the module's own keepInView for a block) would let `scrollSel` avoid
      `scrollIntoView` on the page.
- [ ] I9.6 Live on Killington: Karl's look.
- [x] I9.7 **Extend vs roll by height (lane `agent/tl-edges`, commits `60bbb0b` the zones,
      `f85bca0` the cues; `test_timeline_trim.py` 15 tests, the four timeline files 47
      passed, suite 459 passed + the music-lane test passing alone; Karl, 2026-09-18:
      *"extend a clip while offsetting the next instead of cutting into the next … maybe if
      I do it at the top of the video clip it extends, bottom cuts in?"*):** at every
      interior cut one edge column in `timeline-trim.js`, 16 px centred on the cut line, the
      lane's full height, split at half — the top half extends or shortens the shot and the
      rest of the film moves (left of the line the left shot's out, right of it the right
      shot's in; `tl.setRange`, entry `trim`, tooltip `out 3.30s · +0.30s · the rest
      moves`), the bottom half rolls the cut into the neighbour (entry `roll`, tooltip
      `roll · CLIP_A out 3.30s · CLIP_B in 0.70s`), ⇧ flips before or during the drag; the
      film's first in and last out keep the block's full-height handle; hovering lights the
      half under the pointer (the shot's hue with an arrow into the neighbour, or a neutral
      bar across the cut), a ghost of the first shot that would move sits 0.5 s later, a
      one-line hint under the timeline names the halves (at once for five cut hovers, then
      on a 600 ms dwell); the `?` map gains `▲ edge`, `▼ edge`, `⇧ drag`. Decided by the
      lane: a ⇧ flip cancels the entry and begins it again under the other label, so the one
      undo entry says what the drag ended as; the tooltip's signed number is the change in
      the shot's length (`+` extended, `−` shortened) and reads `the end moves` when nothing
      follows the shot; the tooltips keep the module's `3.30s` spelling; for the right
      shot's in the ghost is the shot after it, or the film's end; the hint's count is the
      editor's (localStorage), not the page load's; the hint overlays the gap the monitor
      card leaves under `#tl` instead of taking a line — a reserved line broke the
      short-window fit of the monitor and its timeline (`test_ui_flow`, 900×380); the outer
      handles keep `ew-resize`, a column drag `col-resize`. The foundation lacked nothing
      this needed; the interior handles are hidden by CSS on `.tl-first` / `.tl-last`
      classes the module sets in film order (`data-i`), never by DOM order.

### M10 · Colour: correct, match, look — the next priority after I9.6 (2026-09-18)

**Why now.** Every render so far is the camera's picture untouched: HERO9 GoPro Color,
8-bit HEVC, full-range 4:2:0 tagged bt709, no log profile on this model (firmware
`HD9.01.01.72.00` on both bins; `ffprobe` + the udta strings, session 15). On Killington
(overcast) the snow sits at L\* 66–78 with a blue cast of b\* −1 to −3.7 and a mean chroma of
2–5: grey, cold, flat. On Copper (bluebird) chroma is 8–25 and 1–11 % of the pixels in the
sunny frames are at or above 98 % — the sky is clipped in camera and nothing brings it back.
A grade is the single largest visible-quality lever left that costs no model call.

**The review — what a colourist does with GoPro footage** (research, session 15; the
sources are in the lab notes at `Projects/roughcut-lab/`): normalise first (a Flat/Log →
709 transform when the profile needs one; HERO9 GoPro Color needs none), then primaries in
a fixed order — exposure, white balance, contrast, saturation — then secondaries, then the
look, and *grade under the look* so the look stays fixed while shots are balanced beneath
it. Snow: expose so it sits at 85–95 IRE with texture, never 100; blue shade is lit by
sky, so warm the shadows rather than the whole frame; skin 40–70 IRE on the +I line
(123° ± 10 on the vectorscope). Shot matching in Resolve/Premiere/FCP is statistics
matching on normalised clips (means and spreads of the colour distribution), and it fails
the same way everywhere: it cannot restore clipped detail and it splits the difference on
large lighting gaps. Creative LUTs are applied at 40–60 % strength, 33³ is the right size
for a 709 input, and 8-bit skies need dither or deband before the final encode. Everything
a pro touches for a three-minute ski edit is: balance per shot, match to a hero, one look
over the film, protect the highlights.

**What the lab proved on this footage** (`grade_lab*.py`, 16 frames across both bins,
numpy/OpenCV in WSL, ffmpeg 7.0 static):

- *Auto-balance from the snow.* Pixels with L\* > 62 and chroma < 14 are the white
  reference; per-channel gains from its mean (clamped ± 15 %), exposure so its luma lands at
  0.86 (clamped 0.75–1.5), a soft shoulder at 0.80 so exposure never adds clipping, blacks
  moved half-way to 3 % (clamped ± 4 %). On Killington that is ×1.16–1.33 exposure with gains
  within ± 3 %; pixels ≥ 98 % go to **0.000 on every frame** (before: up to 0.108 on Copper's
  sun); snow reads white instead of blue-grey. Copper's sunny frames barely move — the auto
  leaves a good picture alone. *Failure found:* the white reference picked an airport
  ceiling and cooled a warm indoor scene — the reference must require outdoor evidence
  (≥ 20 % of the frame, bright, near-neutral) and every shot gets an *off* switch.
- *One LUT per shot, applied by ffmpeg, is exact enough.* A 33³ `.cube` baked from the numpy
  function and applied with `lut3d=interp=tetrahedral` reproduces numpy at **45 dB PSNR**
  (mean error 1/255); 17³ is within 0.1 dB of 33³, so the browser texture can be small.
- *The range trap is real and measured.* `lut3d,format=yuv420p` straight from `yuvj420p`
  moved the frame's YAVG 146.8 → 141.5 — a silent full→limited squeeze. The grey self-test
  (a full-range 128 grey must read 126 after an explicit limited conversion) passes through
  `scale=in_range=full:out_range=limited` and `format=yuv420p`; `zscale` refuses untagged
  input, so swscale does the range work. Today's renders are already tv-range tagged
  (`color_range=tv` on `cut_110ecb13`), so a graded render must land there too.
- *Cost.* 4 s of 4K60 source: preview 1.9 → 2.3 s (+20 %), delivery 11.0 → 14.6 s (+33 %) —
  all x264 CPU; no GPU needed.
- *Shot match.* Reinhard in Lab (mean and spread) moved a cold, dark trees shot toward the
  reference's balance; matching the *spread* with the ratio clamped at 0.75 flattened the
  trees. Match means; clamp spread to 0.85–1.15 or leave it alone.
- *Looks.* Three formula looks (crisp: S-curve 0.22 + vibrance; alpine: + cool-shadow /
  warm-highlight split tone; filmic: softer curve, sat 0.92, warmer, highlights
  desaturated) read as subtle at 480 px and as a definite improvement at 800 px on the
  overcast frames, as a look at ≤ 60 % should. A creative LUT is nothing more than one of
  these baked; the `.cube` drop-in is the same code path.

**Decisions this milestone takes (same rules as EFFECTS.md and decision 5):**

1. **The model never writes filter strings.** Colour is a closed vocabulary with clamped
   numbers: `balance` (gain r/g/b, exposure, knee, lift — the auto fills them, the human
   nudges), `match` (a reference segment id), `look` (a name from the looks library and a
   strength 0–1). The renderer owns every ffmpeg string; the LUT is baked from validated
   parameters, never loaded from a note.
2. **Colour is keyed to a segment id and expressed in parameters, never ranges.** A trim
   re-derives the auto from the samples inside the new in/out; nothing bakes a range in.
   Film-level defaults (`colour.mode`, `colour.look`, `colour.reference`) apply to every
   shot without an override.
3. **One 33³ `.cube` per shot, applied on the part encode.** `assemble.py` re-encodes every
   part already, so the grade costs no generation of quality; the chain is decode →
   `scale=in_range=full:out_range=full` → `format=gbrpf32le` → `lut3d` → `scale=…
   out_range=limited` → `format=yuv420p`, and the grey self-test is a unit test.
4. **The monitor shows the grade without a render.** A WebGL shader over the monitor's two
   `<video>` elements applies the shot's LUT per frame (17³ texture, fetched per segment);
   one key compares before/after. The board previews on the proxy and the render bakes the
   same LUT on the master, so the two agree by construction (EFFECTS.md rule 2).
5. **The auto can only nudge.** Every parameter is clamped to the lab's ranges; it computes
   once per shot from sampled frames (a fixed LUT cannot flicker); it is *on* by default
   for outdoor shots with a white reference and *off* when it cannot find one; the human's
   off switch beats it.
6. **Looks are a library, not a prompt.** `assets/looks/manifest.json` describes each look
   (the three formula presets and any `.cube` Karl drops in) the way the asset manifest
   describes a hitmarker; the Ask chooses by description and strength.

Not taken: HDR/log pipelines (nothing on the HERO9 produces them — a Flat-profile detector
goes under Discovered if Karl ever shoots Flat), per-frame auto (flickers), qualifiers,
windows, tracking, grain and halation (a `deband` on the delivery encode is the one 8-bit
concession worth making, as an option), GPU filters.

**Karl, 2026-09-18, after reading the plan:** *"every new project will need new color
grading and some projects will contain gopro + iphone footage."* Two constraints the items
below must honour:

- **Colour state is per project, never carried over.** Measurements are per clip
  (`<stem>.colour.json`), the auto is per shot, and `colour.mode` / `colour.look` /
  `colour.reference` live in the EDL, so a new bin starts at its own auto with nothing
  inherited; only the looks library is global. The white reference must not assume snow:
  it is "bright, near-neutral, â¥ 20 % of the frame" (snow, sky, walls, sand), with a
  shades-of-grey fallback at half the clamp when no such surface exists, and *off* when
  neither is trustworthy. The clamps are per camera family, not one set for all.
- **Mixed cameras normalise first.** iPhone video is HEVC 10-bit HLG BT.2020 (Dolby
  Vision 8.4) by default and 8-bit 709 only with HDR off; GoPro is 8-bit full-range 709.
  Nothing in M10 runs on HLG. I10.0 records `camera`, `pix_fmt`, `color_transfer`,
  `color_primaries`, `color_range` per clip from the probe (the fields FUTURE_PHASES P2.3
  said the probe keeps for exactly this) and derives a per-clip **normalise** step: HLG/PQ
  â SDR 709 by `zscale` tone mapping (`tonemap=hable`, tagged input so zscale accepts it),
  limited vs full range read from the tag rather than assumed, 10-bit kept in the float
  chain. The **proxies get the same normalise** â an HLG proxy plays dark and washed in the
  monitor today, so `build_proxy` gains the step and the journal re-queues proxies whose
  normalise changed. The balance then sees every clip as SDR 709 and the *match* item is
  where GoPro-vs-iPhone in the same light is reconciled (different WB, saturation, tone);
  the reference shot defaults to the camera with the most screen time. Test bin: the
  synthetic project gains one HLG clip (`-color_trc arib-std-b67`) so the chain is
  exercised without iPhone footage on disk.

- [x] I10.0 (`d84082d`) **Measure, at index time.** Built as `colour.probe` + `measure_clip`;
      **no new journal stage** — the colour file is written right after each proxy (the
      proxy stage and `ensure_proxies`), which is the same moment and keeps the journal's
      release rule and its 40 tests untouched; files are the truth, a missing file is
      measured on the next open. The same commit carries the server contract the lanes
      build against (`GET /api/colour`, `GET /api/lut/{id}`, `colour` on save,
      `--colour-dir` to assemble.py). 25 tests. Original item: `roughcut/colour.py` `probe_colour(clip)` (camera, transfer, primaries, range, bit depth → the normalise step) and `measure(frame)` → luma
      percentiles, clip fraction (≥ 98 %), mean chroma, the white reference (fraction,
      L\*, a\*, b\*, mean RGB) — the lab's `measure()`; `sample(clip, every_s=5)` reads the
      **proxy** (statistics do not need 4K) and writes `<stem>.colour.json`; journal stage
      `colour` after `proxy`, free, part of the released-whole rule. Tests on the synthetic
      project: a grey frame measures neutral, a blue-cast frame reports b\* < 0.
- [x] I10.1 (lane `agent/colour-render` `5155662`, merged `875a5c0`; fixture fix `014d785`)
      **Balance and bake, in the render.** Live on Killington (2026-09-19): the 21-shot,
      205 s cut rendered at preview twice — `mode: off` in 106 s, `auto` + `alpine` 0.5 in
      114 s (+8 %); every shot found its snow (`balance surface ×1.16–1.30`), film YAVG
      144.8 → 164.2, SATAVG 3.1 → 4.0, sheet at `roughcut-lab/out/killington_off_vs_alpine.jpg`
      (snow white, sky kept, nothing blown). Lane decisions kept: a missing `colour` block
      is the auto (decision 4 needs the monitor and the master to agree), every part is
      tagged limited bt709 (verified pixel-identical to untagged on 7.0.2), an HDR source
      is normalised whatever the mode. Copper measured too: ski clips ×0.99–1.16, night and
      indoor as shot; the daylight bar clips exposed the grey-world failure → `GREY_MAX_CHROMA`
      (`1e7f75c`). Original item: `colour.balance_params(samples)` (the
      lab's clamps; `None` without a white reference), `colour.bake_cube(fn, path, n=33)`,
      `colour.apply_chain(cube)` returning the explicit-range `-vf` fragment;
      `assemble.py` derives each part's LUT from the segment's samples (inside in/out, else
      the clip's) when the EDL's `colour.mode` is `auto`, honouring a per-segment override
      keyed by id; `server.py` validates `colour` the way `effects_music` is validated.
      Tests: grey self-test 128 → 126 through the chain; parity numpy vs `lut3d` ≥ 40 dB on a
      synthetic gradient; a trim changes the derived exposure on a frame-split synthetic
      clip; `mode: off` reproduces today's render byte-for-byte.
- [ ] I10.2 **Looks library.** `assets/looks/manifest.json` + the three formula looks
      (`crisp`, `alpine`, `filmic`) as parameter sets, `.cube` files accepted with the same
      manifest entry; strength blends toward identity inside the bake; film-level
      `colour.look` + per-shot override. Test: strength 0 equals balance-only.
- [x] I10.3 (`colour.match_params` in `d84082d`; render half in `5155662`; the buttons in
      `0e959c0`) **Match.** Lab-tested on Killington frames, not yet judged live — the
      Killington cut is one overcast day and every shot balances to the same place, so a
      match there is a no-op; Copper (sun and shade, lift and slope) is where it earns
      its keep. Original item: `colour.match_params(samples, reference_samples)` — Lab means,
      spread clamped 0.85–1.15 — toward `colour.reference` (a segment id; default the
      hero, else the first shot); per-shot *match to previous*. Test: a shifted copy of the
      reference matches back to within 1 L\* / 0.5 a\*b\*.
- [x] I10.4 (lane `agent/colour-ui` `0e959c0`, merged `e480045`; `/grade.js` route in the
      commit after) **The monitor and the inspector.** Lane decisions kept: the canvas is
      `#gradeCanvas` (a `<canvas id="grade">` would shadow `window.grade`), the LUT cache
      drops on every save (a trim re-derives the auto), a 404 from `/api/lut` is the
      identity, colour is not on the undo stack (like music), match buttons and *set as
      reference* toggle off on a second click, the transport keys line clips with an
      ellipsis so `g` fits. 8 browser tests. Original item: `app/static/grade.js`: WebGL LUT over
      `#pv0`/`#pv1` fed by `GET /api/lut/{segment_id}` (17³, the same bake), `G` toggles
      the grade to compare, `requestVideoFrameCallback` drives the upload; the inspector
      gains a *Colour* block — auto on/off, look + strength, warm/cool and brighter/darker
      nudges (± steps on the clamped parameters), *reference* / *match to previous*, and
      the numbers as the witness (snow L\*, cast a\*/b\*, clipped %). Browser test: the
      canvas draws, `G` flips it, a nudge PUTs the override keyed by id.
- [ ] I10.5 **The Ask reaches colour.** A note ("warmer", "less blue", "make it pop",
      "match the lift shot to the summit") → parameters from the vocabulary and the looks
      manifest, as a proposal on the ghost lane; Accept/Discard as ever; the scoped shot
      ask carries a colour clause. Test: an invented look name fails validation.
- [ ] I10.6 **Live on Killington: Karl's look.** The 21-shot cut rendered at preview with
      `mode: auto` + `alpine` at 0.5 beside today's render; his verdict decides the default
      look and whether the auto stays on by default.

**Lab artefacts** (outside the repo, kept): `Projects/roughcut-lab/` — `grade_lab.py`
(measure, balance, looks, bake, parity), `grade_lab2.py` (shoulder balance, range test,
timing, match), `grade_lab3.py` (zoom sheet, grey self-test), the two research reports, the
frames, `out/sheet_looks2.jpg` (16 frames × original/balanced/crisp/alpine/filmic),
`out/sheet_zoom.jpg`, `out/sheet_match.jpg`, `out/kill_CLIP_07.alpine.cube`.

## Lanes in flight

| lane | branch / worktree | scope | state |
|---|---|---|---|
| bin | `agent/bin` · `../roughcut-wt/bin` | M1 | **merged** `2d7182c` + lead wiring; worktree removed |
| floor | `agent/floor` · `../roughcut-wt/floor` | M2.1–2.5 | **merged** `782f24a`; worktree removed |
| journal | `agent/journal` · `../roughcut-wt/journal` | M3.1 | **merged** `53a2578`; worktree removed |
| dictate | `agent/dictate` · `../roughcut-wt/dictate` | M4 | **merged** `50899d0`; worktree removed |
| telemetry | `agent/telemetry` · `../roughcut-wt/telemetry` | M6.1 | **merged** `394556f`; worktree removed |
| floor2 | `agent/floor2` · `../roughcut-wt/floor2` | I2.7 | **merged** `bc090e0`; worktree removed |
| open2 | `agent/open2` | I5.2 UI | **merged** `378ed84`; worktree removed |
| board | `agent/board` | retire the old buttons | **merged** `6c4d249`; worktree removed |
| open3 | `agent/open3` | I5.3 slider, I5.4 picker | **merged** `4b4c441`; worktree removed |
| takes | `agent/takes` | I2.8, I2.4 | **merged** `1e9f83e`; worktree removed |
| floor4 | `agent/floor4` | I7.2, I7.3 | **merged** `7ba33b6`; worktree removed |
| cuts | `agent/cuts` | Karl's feature 1 | **merged** `a4ea6bf` (session 12) |
| settings | `agent/settings` | I8.3 UI | **merged** `6dd9b51`; worktree removed |
| filter | `agent/filter` | I8.2 | **merged** `88cc105`; worktree removed |
| binboard | `agent/binboard` | I8.1 | **merged** `4e6ab8a`; worktree removed |
| timeline | `agent/timeline` | I9.1 foundation | **merged** `139e171`; worktree removed |
| tl-trim | `agent/tl-trim` | I9.2 | **merged** `1a33b0e`; worktree removed |
| tl-keys | `agent/tl-keys` | I9.3 | **merged** `034551b`; worktree removed |
| tl-lanes | `agent/tl-lanes` | I9.4 | **merged** `9ff755d`; worktree removed |
| inspector | `agent/inspector` | I9.5 | **merged** `e3a2c60`; worktree removed |
| tl-edges | `agent/tl-edges` | I9.7 | **merged** `e8818aa`; worktree removed |
| open | `agent/open` · `../roughcut-wt/open` | I5.1, I5.3 | **merged** `6ba1ec0`; worktree removed |

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
- **The old Analyse / Look buttons are retired** (lane `agent/board`): `/` now shows the
  journal's word from `GET /api/index` and a link to `/open` where the two buttons were.
  `POST /api/analyze` and `POST /api/visual` stay — tests and tools use them — so running
  a pass by API alongside the index is still safe (files are truth) but pointless.

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
- 2026-09-08 · + open `6ba1ec0` + floor2 `bc090e0` · **348 passed, 1 skipped** (173.75 s).
  Live on Killington: `/open` shows 12 clips · 43:08 · 2 sessions · telemetry 12/12, the
  paused notice with the lead's reason and the Resume button, the per-clip stage table
  (3 released · 9 queued under the paused rule); `/floor` shows two drag handles on the
  band, the P X U · space · [ ] { } · V · ? key line, the tape legend and lens, and the kept
  range (`2:20.7 → 2:27.3 · 6.7 s` on CLIP_11's backflip) unchanged after three seconds of
  playback. Old lane worktrees removed.
- 2026-09-08 · I5.2 live on Killington (board restarted on `f93ac80`): the themes section
  shows Karl's story from the EDL and the price; one live proposal ($0.20, 50 s, sonnet on
  the judge role) returned six themes — *hitting rocks, blaming the skis* (2 clips), *crashes
  played for laughs* (5), *"send it" / hit it callouts* (6), *chasing the clean footy* (6),
  *the "baby" bit* (3), *"rowdy" trail of the day* (1) — each with a quoted line, and twelve
  names (Spencer / Spencey / Spats, Eric, Carl, Jason, Seth, Mike, Jack, Luke, Ray, JCV).
  **Not kept** — the chips are on the screen for Karl to keep, edit or discard; the EDL's
  `themes` is still empty. The quoted price undershot by 2× and is recalibrated (see
  CHANGELOG · Fixed).
- 2026-09-08 · session 10 · + open2 `378ed84`, board `6c4d249`, open3 `4b4c441`, takes
  `1e9f83e` and the lead's commits · **372 passed, 1 skipped** (205.40 s). Live on
  Killington (board restarted on `190e1a9`): `/` has no Analyse / Look buttons and links
  to `/open`; `/open` shows the themes section with Karl's story, the slider (off — every
  clip was looked at at 4 s), the picker listing killington-neutral · copper-02-2026 (26
  clips) · killington-01-2026; a second live proposal (`b588acd5`, $0.18) — *hitting rocks
  with their skis · the wipeouts · do something sick, send it · the rowdiest trail of the
  day · getting the clean footy · naming jumps after conquistadors · dodging ski patrol* —
  is on the screen and on disk, not kept; `/floor` shows the kept range as a green band on
  the tape with the bridge to the closer strip and `0:01 → 0:14 of 5:19 · 0 % in`, no Caps
  toggle, the microphone hint reading the permission state, and 20 picks in take clusters
  (`CLIP_11.MP4:jump:1` is 4 takes). Space is play / pause.
- 2026-09-08 · I7.1 live: CLIP_04 re-read with prompt version 2 (3 sheets, $0.26, 80 frames
  sampled). Old sidecar backed up to the session scratchpad. Result: the 0:04 glove is now
  `unusable · lens covered by glove`; a hedged "possibly airborne" at 3:20 was **demoted by
  the rule** (`hedged wording`); the 3:52 pole is `pov-gear`; the 4:12 binding and 4:24 glove
  are inside "continuous skiing run", not a backflip and a fall; the summary says "no crashes,
  falls, or jumps observed" apart from **one notable jump 3:44–3:48 (frames 224 · 228)** —
  the wearer's own view going off a rise, which telemetry corroborates (0.28 s freefall at
  224.6, 7.7 g at 225.4) though the wording "skier clearly airborne" overstates what the
  frame shows (no other person in it). The old prompt had missed it. Events rebuilt: CLIP_04
  now carries three notable events (close-look fall 22–23, the jump with frames, faces
  12–20) against the old eight. Suite 380 passed, 1 skipped.
- 2026-09-08 · session 11 · + floor4 `7ba33b6` · **382 passed, 1 skipped** (213.04 s). Live
  on Killington (board restarted on `7ba33b6`, checked headlessly — the in-app browser pane
  hung): CLIP_04's jump pick is first in the queue; the tape says `LOOKED · 80 frames · every
  4 s · 3 sheets`; WHY reads "Skier clearly airborne mid-jump … (frames 3:44.0 · 3:48.0) —
  telemetry: 0.28 s freefall at 0.10 g, 7.7 g" with both times as chips; the witness line
  says `frames 3:44 · 3:48 · high confidence`; clicking the first chip parks the picture at
  224.0 s paused; `0` from 100 s → 1.1 s playing with the whole clip open; `⇧0` → 224.3 s
  inside the band `223.59–228` with the stop at 228.
- 2026-09-13 · session 13 · settings endpoint `e28c231` · **394 passed, 1 skipped** (215.38 s).
  Cuts live on Killington: see I8.4.
- 2026-09-13 · + settings drawer `6dd9b51` + filter `88cc105` · **401 passed, 1 skipped**
  (225.23 s). Filter live on Killington (headless, board on `88cc105`): `/` opens `Filter ·
  kind faces 1 · seen 1 · state undecided 2 · claimed only 2 · has words 0 · has telemetry 1
  · clip CLIP_01 1 · CLIP_04 1` on the round in progress (round 3, two picks left — Karl has
  been culling); *claimed only* → HUD `2 of 2 match`, 7 tape marks dimmed, ↵ steps to the
  next matching pick, Esc clears. No verdict written.
- 2026-09-13 · + binboard `4e6ab8a` · **404 passed, 1 skipped** (223.64 s). The bin live on
  the board (headless, board on `4e6ab8a`): `GET /api/selects` says 43 moments · 0 heroes ·
  50 rejected · 21 used · 5:11 strung out; the board opens on the `kept` tab with 43 rows
  (`CLIP_01 · 0:37.6 → 0:44.3 · 6.7 s` …), 21 of them `in the cut · shot N` and 22 with
  `+ add to cut`; the Project line reads `bin · 43 moments · 0 heroes · 5:11 if strung out ·
  the pass →`; **Cut from the bin** is enabled. Nothing added, nothing asked.
- 2026-09-18 · M9 · I9.0 `c6cdc8d` (suite 407) · I9.1 foundation merged `139e171` (lane's
  own run: `test_timeline_ui.py` 13 passed, suite 421 passed) · board restarted on `da30663`:
  `/timeline/timeline.js` and `.css` served, 21 shots on Killington all carrying server ids.
  `POST /api/snap` keeps ids (`edl_snap.py` copies each segment whole) — the foundation's
  question, answered. Three lanes running: tl-trim, tl-keys, tl-lanes.
- 2026-09-18 · M9 · keys `034551b`, trim `1a33b0e`, lanes `9ff755d` merged; two collisions
  found only in the merged suite and fixed: Esc mid-drag (`db94f5e`) and a drop past the end
  landing on the magnet button (`f1411c5`). Timeline files together: **41 passed**; full
  suite **447 passed, 1 skipped**, plus two music tests that time out under full-suite load
  and pass alone (4.37 s). **Live on Killington** (headless, board serving `f1411c5`'s
  files): 21 shots on V1 with ids, 42 trim handles, 20 roll zones, lanes V1 · markers ·
  ghost (hidden, no proposal) · A1 (Karl's bed) · bin (23 available keeps), `magnet · on`,
  `+` doubles the zoom and `\` fits (4.51 px/s), L L → 4× and K pauses, the `?` map has the
  Timeline section, `#redo` present, no page errors, undo stack untouched.
- 2026-09-18 · + inspector `e3a2c60` · **452 passed, 1 skipped** (287.77 s) with the two
  music tests timing out under load (pass alone, 4.24 s in the lane's run). Inspector live on
  Killington (headless, board serving `e3a2c60`'s files): no `.seg` cards left; selecting a
  block fills `SHOT 3 of 21 · CLIP_01 · 1:31.5 → 1:44.4 · 12.9 s` with its still, eight
  transcript / seen lines and the ± trims, ▶ play, ✎ ask, remove; three blocks → `3 shots
  selected · 41.4 s`; Esc → `select a shot on the timeline — or press ↑ / ↓`; undo stack
  untouched, no page errors.
- 2026-09-18 · + tl-edges `e8818aa` · **459 passed, 1 skipped** (286.45 s) with the music
  panel test timing out under load (passes alone). Edge columns live on Killington (headless):
  20 columns for 21 shots, `EDGE_PX` 16; hovering the top-left quadrant of a cut lights
  `tl-edge lit top left ext` with the ghost strip and the hint *"top edge · extend or shorten
  this shot, the rest moves · bottom edge · roll the cut into the next · ⇧ flips"*; the bottom
  quadrant lights `roll` with no ghost; ⇧ flips it to `ext`; the `?` map has the edge rows;
  no page errors, undo untouched.
