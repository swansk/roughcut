#!/usr/bin/env bash
# browser.sh <editor> <cdp-port> <width> <height>   one long-lived Chromium per editor; every step
# script re-attaches to it (rc.py), so the page keeps its state like a person's open tab.
# Software WebGL by default. RC_GPU=1 puts WebGL on the RTX 3080, but that launch leaks GPU memory
# and loses its GPU process after about two minutes on any page that draws video
# (judge/COPPER-EDITORS.md), so it is not for editors.
# New headless keeps ~200 px of unseen window chrome: the height asked for is the page's.
set -u
R="${RC_ROOT:-/mnt/roughcut/space/roughcut-loop/copper}"
E="$R/editors/$1"; mkdir -p "$E/chrome"
GPU=""; [ "${RC_GPU:-0}" = 1 ] && GPU="--enable-gpu --ignore-gpu-blocklist --use-gl=angle --use-angle=gl-egl"
# A fresh profile's first http load takes ~15 s in this browser (measured; every later one 0.2 s): spend it
# on a JSON endpoint of the editor's own board, so the board's page itself is still a first visit.
WARM=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['url'].rstrip('/') + '/api/backend')" "$E/editor.json" 2>/dev/null || echo about:blank)
CH=$(ls -d /home/karl/.cache/ms-playwright/chromium-*/chrome-linux64/chrome | sort -V | tail -1)
setsid nohup "$CH" --headless=new --no-sandbox --remote-debugging-port="$2" --remote-debugging-address=127.0.0.1 \
  --user-data-dir="$E/chrome" --window-size="$3,$(( $4 + ${RC_CHROME_PAD:-200} ))" --no-first-run --no-default-browser-check \
  --autoplay-policy=no-user-gesture-required --hide-scrollbars \
  $GPU "$WARM" > "$E/chrome.log" 2>&1 &
echo $! > "$E/chrome.pid"
for i in $(seq 1 30); do curl -s "http://127.0.0.1:$2/json/version" > /dev/null && { echo "$1 browser up, cdp $2"; exit 0; }; sleep 1; done
echo "$1 browser did not come up"; tail -5 "$E/chrome.log"; exit 1
