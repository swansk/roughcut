#!/usr/bin/env bash
# One live revision of variant B, end to end.
#
# A script file rather than an inline command on purpose: prose passed through
# `wsl -- bash -lc '...'` from Windows is silently truncated at the first space, so a
# note like "Tighten the opening..." arrives as "Tighten". docs/HANDOFF.md records
# this gotcha; here the note and story are heredocs and never touch a quoting layer.
#
#   wsl -d Ubuntu -- bash /mnt/c/Users/karl/Documents/Projects/roughcut/research/tools/live_ask_b.sh
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${WORK:-$HOME/work}"
EDL="${EDL:-$WORK/edl/B1-variantB-snapped.json}"

mkdir -p "$WORK/edl"
cat > "$WORK/ask-note.txt" <<'NOTE'
Tighten the opening - the airport and plane section runs long. Keep the milk thread
intact and still end on the chug. Give the middle more skiing.
NOTE

cat > "$WORK/ask-story.txt" <<'STORY'
A 2-3 minute edit of a Copper ski trip, for the friends who were there. Emphasis on
the skiing, with just enough of the travel at the top to establish the trip. Loose and
fun rather than cinematic - the people in it are the point, so favour moments with
faces and reactions over pure scenery.

The running joke of the trip is a gallon of milk: Spencer carries it around the
mountain, onto the chairlift, down a tree run, and eventually chugs it at the base
while everyone watches. That thread is the spine of the film and should pay off at the
end - "I became the milkman today, we created a legend".
STORY

echo "=== is the CLI logged in? ==="
claude -p 'Reply with exactly: OK' --output-format json 2>&1 | python3 -c '
import json,sys
d = json.load(sys.stdin)
print("  is_error:", d.get("is_error"), "| result:", repr(d.get("result"))[:60])
print("  usage:", d.get("usage"))
' || { echo "smoke call failed"; exit 1; }

echo
echo "=== live ask ==="
exec uv run --quiet "$REPO/research/tools/ask.py" \
  --edl "$EDL" \
  --sidecars "$WORK/audio" \
  --note-file "$WORK/ask-note.txt" \
  --story-file "$WORK/ask-story.txt" \
  -o "$WORK/edl/B-proposal.json"
