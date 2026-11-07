# Wave 20: footage understanding, hybrid (Karl's K5 + K6)

Planned 2026-11-07 from the bake-off in `research/R12-understanding/` (research, local 3080 results, Claude-checker
results, Karl's checked Killington test set in `eval/`). Builds after the weekly usage reset (Karl: "research now, build
Monday"). Deploy = merged on `redesign/2026-10-14` and Karl's 8766 preview restarted on it (K6 answer); main / 8765 stay his.

## What the bake-off decided (Killington, Karl-checked set: 28 positives, 19 negatives, 22 Find queries)

| Who finds / settles it | Recall (high-conf events) | Airs | Falls | False alarms / negative-min | Negatives asserted |
|---|---|---|---|---|---|
| Today's Claude index (4 s sheets + close looks) | 0.33 | 0.29 | 0.60 | 5.09 | 13 of 19 |
| GoPro telemetry candidates alone ($0, 7 s/bin) | **0.78** | **0.86** | 0.60 | 2.74 | — |
| Local Qwen3.5-4B check on telemetry windows | 0.22 @0.5 | 0 (coin flip) | AUROC 0.86-0.94 | 0-0.78 | — |
| Claude Sonnet checker: one levelled 8 fps strip + telemetry facts | 15/17 windows | 6/7 | 4/4 | — | rejected 5 of 6 old false claims |

Find hit@3 (any overlap, 19 queries): today's lexical 0.58, local PE-Core 0.68, SigLIP 2 0.37. A dense local VLM pass
costs ~1 GPU-hour per footage-hour (bnb nf4, no vLLM) and never reported an air: captions only, not detection.

**The design (the winner on the test set, not on paper):**
1. **Telemetry decides when.** R11 rules as first-class timed events: air = |a| < 0.5 g for >= 0.25 s then landing
   > 2.5 g; impact > 5 g; speed drop >= 3 m/s in 3 s; stopped / lift from GPS. Each event carries its numbers.
2. **Local (3080) settles what it can:** Qwen3.5-4B (bnb nf4, ~4.4 GB) yes/no CHECK at 8-10 fps on levelled frames for
   falls / crashes / camera handling, threshold ~0.2; PE-Core (`timm/PE-Core-L-14-336`) embeddings at 2 fps for Find;
   levelling from GRAV roll (+roll upright mount, -roll inverted mount; the -180 deg display rotation on CLIP_04-12 honoured).
   Local never settles an air.
3. **Claude checks, it does not scan:** one levelled 8 fps strip (24 cells, <= 1568 px) + the telemetry facts per
   event; verdict confirm / reject / retime / unsure. Sonnet (Opus showed no gain at 2x). Routed in Hybrid: every air,
   every fall / crash in the 0.2-0.9 band or local-vs-telemetry disagreement, every event the cut uses, the top 3 heroes
   always. Low confidence = "unsure", shown to Karl, never asserted.
4. **No unsupported event claims reach the planner or the board:** every old index jump / fall / crash without telemetry
   support is re-checked or demoted.
5. **The planner reads better text:** a per-clip "felt" block (airs with air time and landing g, impacts, speed, stops,
   lift rides), checked events with who settled them, captions; Find = lexical first, PE-Core top-k second, Claude reranks
   the union.
6. **Per-bin mode** (Index panel): Hybrid (default) / Local only (events marked "unchecked") / Claude only (today's
   pipeline + the checker). The first pass and the Index panel say, in words and numbers, what ran at what granularity
   and how sure each moment is (K5).

## Lanes (by file ownership; Sonnet; ports 9110-9134)

| Lane | Owns | Done = |
|---|---|---|
| TELEM | `roughcut/telemetry_events.py` (new, from `research/tools/telemetry.py` + bench `telemetry_lib.py`), `events.py` merge, `revise.py` felt block | on Killington: 6 of 6 R11 airs and the CLIP_01 70 s fall in the planner prompt with numbers; prompt tokens +<= 2k/bin; tests on the synthetic fixture |
| LOCALGPU | `roughcut/localvlm/` (frames: NVDEC + rotation + levelling + sharpest-of-5; Qwen3.5-4B CHECK; PE-Core embed), an index stage `local` in server.py's job queue (GPU lock, nice 5, cancellable, resumable), `scripts/install-local-models.sh` | the stage runs on Killington in <= 15 GPU-min; CHECK fall AUROC >= 0.85 on the test set; degrades to "not available" with no GPU (tests pass on CPU-only CI) |
| VERIFY | `roughcut/verify.py` (strip builder + Claude checker + routing), the per-bin mode setting, demotion of unsupported claims | test set: >= 6/7 airs and 4/4 falls confirmed, >= 5/6 false claims rejected; a FALSE telemetry air (synthetic) is rejected (the untested risk); calls per Killington first pass <= 40 |
| FIND3 | `roughcut/find.py`: lexical + PE-Core union, Claude rerank | hit@3 (any overlap) >= 0.75 on the 19 queries |
| K5UI | `firstpass.js`, `indexpanel.js`, flow words | while running and after: each step's what / granularity / numbers; each moment's certainty (telemetry / local / Claude / unchecked); a run on an existing index says it re-ranked only |
| EVAL | `tools/eval_understanding.py` + `research/R12-understanding/eval` as the gate's understanding check (scored offline from sidecars, no model calls) | the gate prints recall / false alarms / Find hit@3; a regression fails it |

Open for Karl: do other riders' airs count as events (CLIP_10 25 s: a skier flies over the camera; the checker said
"not the wearer")? The speaker-label model (pyannote community-1) needs his HuggingFace token: deferred.
Audio tags (EfficientAT) need wind masking first: deferred to a later wave.
