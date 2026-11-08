#!/usr/bin/env bash
# Install Roughcut's local-GPU understanding stack (wave 20): the heavy Python environment
# and the two models. Safe to re-run; nothing is downloaded that is already there.
#
#   scripts/install-local-models.sh            install everything
#   scripts/install-local-models.sh --check    report what is installed, change nothing
#
# Where things go (override with the environment):
#   ROUGHCUT_LOCALGPU_VENV   ~/.cache/roughcut/localgpu-venv     the environment (uv, torch cu128)
#   ROUGHCUT_MODELS          ~/.cache/roughcut/models             qwen35-4b-bnb4/ + hf/ (PE-Core)
#   ROUGHCUT_MODELS_FROM     ~/roughcut-loop/understand           reuse models already built there
#                                                                (symlinked, not copied)
# Needs: uv, an NVIDIA GPU with >= 10 GB free (RTX 3080 12 GB is the target), ~12 GB of disk.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv="${ROUGHCUT_LOCALGPU_VENV:-$HOME/.cache/roughcut/localgpu-venv}"
models="${ROUGHCUT_MODELS:-$HOME/.cache/roughcut/models}"
from="${ROUGHCUT_MODELS_FROM:-$HOME/roughcut-loop/understand}"
check_only=0
[ "${1:-}" = "--check" ] && check_only=1

say() { printf '%s\n' "$*"; }
export UV_PROJECT_ENVIRONMENT="$venv"

if [ "$check_only" = 1 ]; then
  [ -x "$venv/bin/python" ] && say "venv:   ok ($venv)" || say "venv:   missing ($venv)"
  [ -d "$models/qwen35-4b-bnb4" ] && say "qwen:   ok" || say "qwen:   missing"
  ls "$models"/hf/hub/models--timm--PE-Core-L-14-336/snapshots/* >/dev/null 2>&1 && say "pe-core: ok" || say "pe-core: missing"
  command -v nvidia-smi >/dev/null && nvidia-smi -L | head -1 || say "gpu:    no nvidia-smi"
  exit 0
fi

command -v uv >/dev/null || { say "uv is required (https://docs.astral.sh/uv/)"; exit 1; }
command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1 || { say "no NVIDIA GPU found: the local stage needs one"; exit 1; }

say "1/3 environment -> $venv"
uv sync --project "$here/localgpu"

mkdir -p "$models"

say "2/3 Qwen3.5-4B (nf4) -> $models/qwen35-4b-bnb4"
if [ -d "$models/qwen35-4b-bnb4" ]; then
  say "  already there"
elif [ -d "$from/models/qwen35-4b-bnb4" ]; then
  ln -s "$from/models/qwen35-4b-bnb4" "$models/qwen35-4b-bnb4"; say "  linked from $from"
else
  # download the official bf16 weights (~9 GB), quantise once to nf4 (double-quant, bf16
  # compute, vision tower + lm_head left in bf16) and keep only the 3 GB result
  export HF_HOME="$models/hf"
  "$venv/bin/python" - "$models" <<'PY'
import sys, torch
from transformers import AutoProcessor, AutoModelForImageTextToText, BitsAndBytesConfig
out = f"{sys.argv[1]}/qwen35-4b-bnb4"
q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                       bnb_4bit_compute_dtype=torch.bfloat16, llm_int8_skip_modules=["visual", "lm_head"])
m = AutoModelForImageTextToText.from_pretrained("Qwen/Qwen3.5-4B", device_map="cuda", dtype=torch.bfloat16, quantization_config=q)
m.save_pretrained(out); AutoProcessor.from_pretrained("Qwen/Qwen3.5-4B").save_pretrained(out)
print("saved", out, "peak GB", torch.cuda.max_memory_allocated() / 1e9)
PY
  rm -rf "$models"/hf/hub/models--Qwen--Qwen3.5-4B
fi

say "3/3 PE-Core-L/14-336 (Find embeddings) -> $models/hf"
if [ -e "$models/hf" ]; then
  say "  hf cache already there"
elif [ -d "$from/hf" ]; then
  ln -s "$from/hf" "$models/hf"; say "  linked from $from"
else
  HF_HOME="$models/hf" "$venv/bin/python" -c "import open_clip; open_clip.create_model_and_transforms('hf-hub:timm/PE-Core-L-14-336'); print('downloaded')"
fi

say "check:"
PYTHONPATH="$here/localgpu" ROUGHCUT_MODELS="$models" "$venv/bin/python" -m rcgpu status || true
