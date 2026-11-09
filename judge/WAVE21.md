# Wave 21: verdict 16's punch list

Verdict 16 (FAIL on `b2cbc31`: 9, 5, 9, 9, 9, 7, 8, 9, 7, 8, 8, 9, 9, 8, 9, 8). Blockers: K2-K6 (Karl's; K5/K6 have
concrete gaps), K1 broken again at 1440×900 (the first-pass panel's Re-run below the window after edits + new clips), the
judge's full gate RED (perf at load 7.9 beside a first pass; sharpen and half-speed frame holds; the suite outran gate.py's
3600 s timeout with no summary), and footage without telemetry losing real falls with no way to check claims from the UI.
Punch 9 (rubric log + FOR-KARL) was done by the lead (`0f1337e`). All lanes branch from the commit that adds this file.

| Lane | Punch | Owns |
|---|---|---|
| FPPANEL | 1, 6, 7 (UI half), 10 | firstpass.js/.css `paintPanel` (max-height, scrolling body, pinned foot, collapsed keeps list), flow.py `pass_account` + server `api_firstpass_account` / `bin_certified` (running words with models and counts, the 3080 and Claude lines, speed ≤ 300 ms), the contradicted-chip hover, indexpanel.js/.css (mode select full name + visible sentence, Local column progress), their tests (test_k5_*, test_firstpass_ui, test_workspace overflow for `.ix-foot`) |
| EVALREV | 2, 8 (gate half) | tools/eval_understanding.py, tools/gate.py (`understanding_check` scoring the rev's own pass on a frozen read-only input copy with recorded checker verdicts, AMBER skip; the suite timeout ending in a RED summary + test_gate_status pin; the suite budget), research/R12-understanding/eval/baseline.json + a recorded-verdicts file |
| CHECKER | 3, 4 (routing half), 7 (plan half) | roughcut/verify.py (`settle`/`annotate_event`/`demote`/`route`: a sensor-proven air Claude rejects becomes `unsure`, still felt; the checker prompt leads with the free-fall numbers; every local "other air" routes to Claude; `apply_to_plan`: a contradicted moment is not proposed or is listed in Things to check), the planner hint in revise.py / firstpass.py for that, test_verify.py |
| OTHERAIR | 4 (scan half), S note | roughcut/localvlm.py + localgpu/rcgpu (a levelled frame-scan "is anyone in frame airborne" over high-motion windows without a telemetry air, `who: other` candidates), events.py `who` plumbing, the local model ids into roughcut/config.py's table |
| CLAIMSUI | 5 | aiprompt.js (the `audit` item), app.js (the seen-tab audit button) driving GET /api/verify `claims_check` + POST /api/verify/claims (price first), server `/api/visual/audit` retired or aliased, new app/tests/test_verify_ui.py |
| GRADE | 8 (sharpen half) | grade.js's sharpen stage + the page→worker hand-off (verdict 15 punch 4); quiet-box A/B only — launched after the others, at load ≤ 4 |

Shared: server.py (FPPANEL the account handlers, CLAIMSUI the audit route, CHECKER only via verify.py, EVALREV none).
verify.py is CHECKER's alone; OTHERAIR exposes candidates CHECKER routes (document the shape early). Ports: FPPANEL
9160-9164, EVALREV 9165-9169, CHECKER 9170-9174, OTHERAIR 9175-9179, CLAIMSUI 9180-9184, GRADE 9185-9189.
Box rule as wave 19/20: tests nice -n 19 (but NEVER run the full gate under nice: test_yield breaks), one Playwright at a
time per lane, GPU via ~/.cache/roughcut/gpu.lock, no model calls except CHECKER (≤ 60) and OTHERAIR (local only).
