#!/usr/bin/env bash
# browser.sh <editor> <cdp-port> <width> <height>   one long-lived Chromium (WebGL on the RTX 3080)
# (new headless keeps ~200 px of unseen window chrome: the height asked for is the page's)
# per editor; every step script re-attaches to it (kit/rc.py), so the page keeps its state like a
# person's open tab.
set -u
R=/mnt/roughcut/space/roughcut-loop/copper
E="$R/editors/$1"; mkdir -p "$E/chrome"
CH=$(ls -d /home/karl/.cache/ms-playwright/chromium-*/chrome-linux64/chrome | sort -V | tail -1)
setsid nohup "$CH" --headless=new --no-sandbox --remote-debugging-port="$2" --remote-debugging-address=127.0.0.1 \
  --user-data-dir="$E/chrome" --window-size="$3,$(( $4 + ${RC_CHROME_PAD:-200} ))" --no-first-run --no-default-browser-check \
  --autoplay-policy=no-user-gesture-required --hide-scrollbars \
  --enable-gpu --ignore-gpu-blocklist --use-gl=angle --use-angle=gl-egl about:blank > "$E/chrome.log" 2>&1 &
echo $! > "$E/chrome.pid"
for i in $(seq 1 30); do curl -s "http://127.0.0.1:$2/json/version" > /dev/null && { echo "$1 browser up, cdp $2"; exit 0; }; sleep 1; done
echo "$1 browser did not come up"; tail -5 "$E/chrome.log"; exit 1
