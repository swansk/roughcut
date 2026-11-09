"""Where models live and the fixed numbers of the recipe (bake-off: research/R12-understanding)."""
from __future__ import annotations

import os
from pathlib import Path

from roughcut import config as _rc  # noqa: E402  (stdlib-only; the model table lives there)

_QWEN = _rc.local_model(_rc.LOCAL_VLM)   # Qwen3.5-4B, nf4 (double-quant, bf16 compute; vision tower + lm_head bf16)
QWEN_DIR = _QWEN["dir"]
QWEN_REPO = _QWEN["repo"]
PE_REPO = _rc.local_model(_rc.LOCAL_EMBED)["repo"]

GRID_FPS = 10.0                       # CHECK frames
EMBED_FPS = 2.0                       # Find embeddings
FRAME_W = 448                         # check-frame width
SHARP_HALF = 2                        # sharpest of +-2 source frames
CHECK_HALF_S = 1.5                    # a window is +-1.5 s round a moment
from .rules import FALL_THRESHOLD  # noqa: E402,F401  P(yes) for "falls" (bake-off AUROC); 0.2-0.9 is Claude's band
SCAN_FROM_P = 0.15                    # run the coarse-to-fine timing scan above this
LEVEL_MAX_DEG = 80
OTHER_AIR_P = 0.5                     # a scanned window at or above this P(yes) becomes a `who: other` air candidate
OTHER_AIR_SCAN_FROM_P = 0.3           # the 1 s timing scan runs only above this (it is most of the check's cost)
OTHER_AIR_SPAN_S = 1.5                # the candidate's start..end round its peak


def models_dir() -> Path:
    env = os.environ.get("ROUGHCUT_MODELS")
    return Path(env) if env else Path.home() / ".cache" / "roughcut" / "models"


def setup_env() -> None:
    """Point the Hugging Face cache at the models folder and keep the run offline: a
    missing model is an install problem, not something to fetch mid-index."""
    os.environ.setdefault("HF_HOME", str(models_dir() / "hf"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def qwen_path() -> Path:
    return models_dir() / QWEN_DIR


def pe_cached() -> bool:
    hub = models_dir() / "hf" / "hub" / ("models--" + PE_REPO.replace("/", "--"))
    return (hub / "snapshots").is_dir() and any((hub / "snapshots").iterdir())
