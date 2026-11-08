"""Killington's numbers for the K5 words (wave 20): what the index holds on Karl's bin
(43 min, 12 clips), the way `server.account_inputs` sums them, and the first pass's
`run` in each state. Shared by test_k5_words.py (the words) and test_k5_ui.py (the panel)."""

from __future__ import annotations

KILLINGTON = {
    "clips": 12, "minutes": 43.0,
    "heard": {"clips": 12, "words": 1666, "model": "large-v3", "timed": True},
    "looked": {"clips": 12, "frames": 651, "intervals": {"4.0": 12}, "interval_s": 4.0,
               "width": 320, "cols": 6, "rows": 5, "model": "claude-sonnet-4-5"},
    "close": {"clips": 12, "windows": 36, "seconds": 420.0, "interval_s": 1.0, "width": 480,
              "model": "claude-opus-4-1"},
    "deep": {"spans": 1}, "motion": {"clips": 12, "hz": 10.0}, "moments": 188,
}
EMPTY_BIN = {"clips": 12, "minutes": 43.0, "moments": 0,
             "heard": {"clips": 0}, "looked": {"clips": 0, "interval_s": 4.0},
             "close": {"clips": 0}}

# the bin's 188 moments by certainty on Karl's bin before wave 20 (K5: "1 confirmed, 89 never
# checked, 65 close-look only, 22 unsupported, 6 contradicted") plus what the new lanes add
BIN_COUNTS = {"claude": 1, "unchecked": 89, "once": 65, "unsupported": 22, "contradicted": 6,
              "total": 183}
HYBRID_COUNTS = {"felt": 6, "local": 9, "claude": 14, "once": 40, "unchecked": 80,
                 "unsupported": 20, "contradicted": 6, "total": 175}
CUT_COUNTS = {"felt": 3, "local": 2, "claude": 5, "unchecked": 6, "total": 16}

RUNS = {
    "never-run": {"state": "never-run"},
    "running-index": {"state": "running", "stage": "index",
                      "counts": {"words": "heard 3/12 · looked at 1/12 · previews 2/12"}},
    "running-picks": {"state": "running", "stage": "picks"},
    "running-cut": {"state": "running", "stage": "cut",
                    "cut_detail": "choosing among 40 picks"},
    "done-existing": {"state": "done", "index_ran": False, "index_complete": True,
                      "picks": 40, "shots": 19},
    "done-fresh": {"state": "done", "index_ran": True, "index_complete": True,
                   "picks": 40, "shots": 19},
}
