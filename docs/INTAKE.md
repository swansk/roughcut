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

_2026-09-07, session 8, later._ On `main`: foundations (M0), the bin (M1, merged + wired),
the journal module (I3.1, merged), dictation (M4, merged). The telemetry study (I6.1) is done
on its lane and merging next; the floor UI (M2) is still building on `agent/floor`. Nothing
verified live on Killington yet — I2.6 is the next thing that needs a human at the board.
Next after the floor merges: I3.2 (wire the journal into the server), then M5.

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

- [ ] I2.1 `/floor` page: HUD, picture with margins (context left, witnesses right), reason
      line with conflicts, whole-clip tape (picks, waveform placeholder, telemetry trace when
      present), zoomed strip with words / sentence bars, kept-range = watched, snapping via the
      sidecar word spans, stamps, key line. DoD: browser tests (`test_floor_ui.py`).
- [ ] I2.2 Keys per decision 2, including `⇧X` (reject the rest of this clip), hold-space,
      `[ ] { }` snapping, `← →` frame step at the active edge, `E` evidence drawer, `.` more.
- [ ] I2.3 Rounds of 40 with frozen queue, closing card (counts, "if strung out", arrivals since
      round start, unlooked stretch warning; free default ↵ = play the bin; Assemble on `A` with
      the price). Resume from `floor.position`.
- [ ] I2.4 Compare takes (Survey view) for clustered picks; batch reject via filter.
- [ ] I2.5 Dictation UI: hold `V` → MediaRecorder → `POST /api/dictate` → note attached; clip
      audio ducked while held; `N` edits.
- [ ] I2.6 Live verification on Killington: open `/floor`, cull a round, notes land in the EDL,
      accept nothing by accident. Record numbers here.

### M3 · The journal (lane `agent/journal`, pure module first)

- [x] I3.1 `roughcut/journal.py`: per-clip stages (probe, telemetry, asr, proxy, look, close,
      picks) with done/running/failed/queued, attempts, cost; reconcile with sidecars on disk
      (files are truth, the journal is the plan); resume = re-queue what isn't done; priority
      score from the free stages; `released(clip)` = every stage done. DoD: unit tests incl. a
      simulated crash mid-stage and an added clip. — commit `f7555a2` on `agent/journal`
      (`test_journal.py` 18 passed; suite 283 passed)
- [ ] I3.2 Wire into the server: one `POST /api/index` (start/resume) replacing the two
      buttons' orchestration; per-clip release feeding `/api/picks`'s `released`; failure states
      (backoff on rate limit, park after 3 failures, cap pauses priced stages). DoD: API tests
      with stubbed tools, then a live unattended run on Killington with a kill mid-run.
- [ ] I3.3 Added footage: new stems → free stages → queued → delivered as a round; relink by
      hash. DoD: API test adding a clip mid-run.

### M4 · Dictation (lane `agent/dictate`)

- [x] I4.1 `roughcut/dictate.py` + `research/tools/dictate.py`: webm/opus or wav in → ffmpeg →
      faster-whisper (same model family as the audio pass, GPU when present) → text; `names`
      from the brief seed the prompt. DoD: unit test with a synthetic tone + a stubbed model;
      one live check that a spoken sentence comes back. — commit `ac7dca2` (`test_dictate.py`
      9 passed + the live test on a 2 s tone: `small` on cuda/float16, 1.3 s warm in the tool,
      4.7 s end to end; a spoken sentence still needs a human at a mic — I2.6 covers it).
- [x] I4.2 `POST /api/dictate` real (≤ 30 s, 413 above), returns `{text, latency_ms}`. —
      commit `ac7dca2` via the pre-written endpoint (raw body, not multipart, by design: the
      floor posts a Blob). The 413 is on bytes (6 MB); a decoded length over 30 s raises
      `dictate.TooLong`, a `NotAvailable`, so it answers 501 until the lead maps it to 413.

### M5 · The open screen (after M2/M3)

- [ ] I5.1 Contact sheet of the folder with free flags (junk band, side-data, telemetry
      present), sessions by the 4 h rule.
- [ ] I5.2 Themes proposed from transcripts (one judge-role call), chips + dictation.
- [ ] I5.3 Granularity slider re-pricing live; workers + cap in settings; "Index" starts the
      journal.
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
- [ ] I6.2 Only if R11 lands: `felt` witness in picks (numbers only), corroboration term in
      `events.py` with a fitted weight, gyro orientation as a check on `seen` flip claims.

## Lanes in flight

| lane | branch / worktree | scope | state |
|---|---|---|---|
| bin | `agent/bin` · `../roughcut-wt/bin` | M1 | **merged** `2d7182c` + lead wiring; worktree can be removed |
| floor | `agent/floor` · `../roughcut-wt/floor` | M2.1–2.5 | running 2026-09-07 |
| journal | `agent/journal` · `../roughcut-wt/journal` | M3.1 | **merged** `53a2578`; worktree can be removed |
| dictate | `agent/dictate` · `../roughcut-wt/dictate` | M4 | **merged** `50899d0`; worktree can be removed |
| telemetry | `agent/telemetry` · `../roughcut-wt/telemetry` | M6.1 | done on the lane (`f806e13`, `296fede`) — merge pending |

Lanes touch disjoint files by design: `bin` → `revise.py`, `selects.py`, tests; `floor` →
`app/static/floor.*`, `test_floor_ui.py`; `journal` → `journal.py`, `test_journal.py`;
`dictate` → `dictate.py`, `research/tools/dictate.py`, tests; `telemetry` → `research/`.
`app/server.py` is the lead's file: endpoints are pre-written; lanes fill the modules behind
them. Every lane adds its own CHANGELOG bullet; integration keeps all of them.

## Discovered

- (none yet)

## Verification log

- 2026-09-07 · foundations · suite: see the I0 commits' bodies.
