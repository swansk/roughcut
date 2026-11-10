#!/usr/bin/env bash
# fork.sh <editor> <port> <cdp-port> <width> <height> [<from-editor>]
# A new editor starting from another's project as it is now (default: cold): its own copy of the
# work dir (paths inside rewritten to the copy), its own board on the Copper folder, its own tab.
set -eu
R=/mnt/roughcut/space/roughcut-loop/copper
N="$1"; PORT="$2"; CDP="$3"; W="$4"; H="$5"; FROM="${6:-cold}"
E="$R/editors/$N"
[ -e "$E/work" ] && { echo "$N already has a work dir"; exit 9; }
mkdir -p "$E/out/shots"
cp -a "$R/editors/$FROM/work" "$E/work"
n=0
while IFS= read -r f; do
  sed -i "s#editors/$FROM/work#editors/$N/work#g" "$f"; n=$((n+1))
done < <(grep -rlI --include='*.json' --include='*.jsonl' --include='*.txt' "editors/$FROM/work" "$E/work" 2>/dev/null || true)
echo "$N: copied $(du -sh "$E/work" | cut -f1), rewrote paths in $n files"
echo "{\"name\": \"$N\", \"url\": \"http://127.0.0.1:$PORT/\", \"cdp\": \"http://127.0.0.1:$CDP\", \"viewport\": [$W, $H]}" > "$E/editor.json"
"$R/kit/serve.sh" "$N" "$PORT" /mnt/roughcut/footage/copper-02-2026
"$R/kit/browser.sh" "$N" "$CDP" "$W" "$H"
