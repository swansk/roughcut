#!/usr/bin/env bash
# Render whatever ask.py last proposed, and verify it the same way every other cut
# in this project has been verified.
#
# Script file rather than an inline command for the reason recorded in
# docs/HANDOFF.md: prose and paths passed through `wsl -- bash -lc '...'` from
# Windows get mangled.
#
#   wsl -d Ubuntu -- bash /mnt/c/Users/karl/Documents/Projects/roughcut/research/tools/render_proposal.sh
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${WORK:-$HOME/work}"
EDL="${EDL:-$WORK/edl/B-proposal.json}"
OUT="${OUT:-$WORK/cuts/copper-variantB-asked.mp4}"
DEST="/mnt/c/Users/karl/Documents/Roughcut Labeling/cuts"

[ -f "$EDL" ] || { echo "no proposal at $EDL — run live_ask_b.sh first"; exit 1; }

echo "=== the note this cut answers ==="
python3 -c "
import json; d = json.load(open('$EDL'))
print(' ', d.get('revision_note', '(none recorded)'))
print()
print('rationale:', d.get('revision_rationale', '(none)')[:400])
"

echo
echo "=== render ==="
uv run --quiet "$REPO/research/tools/assemble.py" "$EDL" \
  --footage ~/footage/copper-02-2026 --sidecars "$WORK/audio" -o "$OUT" || exit 1

echo
echo "=== verify ==="
V=$(ffprobe -v error -select_streams v:0 -show_entries stream=duration -of csv=p=0 "$OUT" | tr -d ',')
A=$(ffprobe -v error -select_streams a:0 -show_entries stream=duration -of csv=p=0 "$OUT" | tr -d ',')
R=$(ffprobe -v error -select_streams v:0 -show_entries stream_side_data=rotation -of csv=p=0 "$OUT" | tr -d ',\n')
python3 -c "
v, a = float('$V'), float('$A')
print(f'  duration   {v:.2f}s')
print(f'  A/V drift  {abs(v-a)*1000:.0f}ms', '(under one frame)' if abs(v-a) < 0.042 else 'FAIL')
print(f'  rotation   ' + ('${R}' or 'none'))
"
ffmpeg -v info -nostdin -i "$OUT" -filter:a ebur128 -f null - 2>&1 \
  | grep -A2 "Integrated loudness:" | grep "I:" | sed 's/^/  loudness  /'
ffmpeg -v info -nostdin -i "$OUT" -vf "blackdetect=d=0.5:pix_th=0.10" -f null - 2>&1 \
  | grep -q blackdetect && echo "  black runs FOUND" || echo "  black runs none"

mkdir -p "$DEST" && cp "$OUT" "$DEST/" && echo
echo "copied to Documents\\Roughcut Labeling\\cuts\\$(basename "$OUT")"
echo "watch it against copper-variantB.mp4 — same footage, one asked for, one dragged."
