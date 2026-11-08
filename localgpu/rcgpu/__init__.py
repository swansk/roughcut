"""rcgpu — the local-GPU half of Roughcut's footage understanding.

Run as `python -m rcgpu <command>` inside the `localgpu` environment (see
scripts/install-local-models.sh). The app side is `roughcut/localvlm.py`; this package
never imports the app, only the stdlib-only `roughcut.gpulock`.

Commands: status · frames · check · embed · index · search.
"""

import sys as _sys
from pathlib import Path as _Path

# `roughcut.gpulock` is stdlib-only; reach it when run from a checkout without installing anything
_repo = _Path(__file__).resolve().parents[2]
if (_repo / "roughcut" / "gpulock.py").exists() and str(_repo) not in _sys.path:
    _sys.path.append(str(_repo))
