#!/usr/bin/env bash
# serve.sh <editor> <port> <footage-dir>   one board for one simulated editor, on its own work dir
set -u
R="${RC_ROOT:-/mnt/roughcut/space/roughcut-loop/copper}"
SNAP="${RC_SNAP:-$R/snap}"      # the code the board runs: a frozen snapshot, or a worktree
E="$R/editors/$1"; mkdir -p "$E/work" "$E/out/shots"
cd "$SNAP" || exit 9
env -u ANTHROPIC_API_KEY ROUGHCUT_LEDGER="$R/ledger.jsonl" setsid nohup uv run app/server.py \
  --footage "$3" --work "$E/work" --port "$2" > "$E/server.log" 2>&1 &
echo $! > "$E/server.pid"
for i in $(seq 1 90); do curl -s -o /dev/null "http://127.0.0.1:$2/" && { echo "$1 board up on $2 (pid $(cat "$E/server.pid"))"; exit 0; }; sleep 1; done
echo "$1 board did not answer on $2"; tail -5 "$E/server.log"; exit 1
