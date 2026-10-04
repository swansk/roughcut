#!/usr/bin/env bash
# Sign the Claude CLI in for Roughcut with a token that lasts a year.
#
# Run it from Windows by double-clicking claude-signin.cmd (or pasting that file's path
# into cmd / PowerShell); from WSL, `bash -l scripts/claude-signin.sh`.
#
# Why a token and not `claude auth login`: the login's access token is short-lived and
# its refresh token rotates, and Roughcut runs several `claude -p` at once — when they
# refresh together, a loser wipes the saved sign-in (see inference.cli_env). A
# `claude setup-token` token is never refreshed. It is saved where Roughcut reads it,
# outside the repo, readable only by you; nothing else on the machine changes.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

token_file="${ROUGHCUT_CLAUDE_TOKEN_FILE:-$HOME/.config/roughcut/claude-token}"
pattern='sk-ant-oat[0-9]*-[A-Za-z0-9_-]{20,}'

if ! command -v claude >/dev/null; then
  echo "The Claude CLI is not installed in WSL (expected ~/.local/bin/claude)." >&2
  exit 1
fi

echo "Opening the Claude sign-in. Approve it in the browser; if no browser opens,"
echo "copy the link it prints, then paste the code back here."
echo

# `script` keeps the sign-in interactive while recording what it prints, so the token
# can be picked up without copying it by hand.
umask 077
log="$(mktemp)"
trap 'rm -f "$log"' EXIT
script -q -e -c "claude setup-token" "$log" || true
token="$(sed -e 's/\x1b\[[0-9;?]*[A-Za-z]//g' "$log" | tr -d '\r' \
         | grep -oE "$pattern" | tail -n1 || true)"

check() {
  CLAUDE_CODE_OAUTH_TOKEN="$1" claude -p "Reply with exactly: OK" 2>&1 | grep -qx OK
}

if [ -z "$token" ] || ! check "$token"; then
  echo
  read -rsp "Paste the token it printed (starts sk-ant-oat), then press Enter: " token
  echo
  token="$(printf '%s' "$token" | tr -d '[:space:]')"
  if ! [[ "$token" =~ ^$pattern$ ]]; then
    echo "That does not look like a Claude token — nothing was saved." >&2
    exit 1
  fi
  echo "Checking it works..."
  if ! check "$token"; then
    echo "The CLI refused that token — nothing was saved." >&2
    exit 1
  fi
fi

mkdir -p "$(dirname "$token_file")"
printf '%s\n' "$token" > "$token_file"
chmod 600 "$token_file"
echo "Signed in. Saved to $token_file (good for a year)."

# Clear the board's banner if it is running; harmless if it is not.
if curl -fsS -m 5 -X POST "http://localhost:${ROUGHCUT_PORT:-8765}/api/backend/probe" \
     >/dev/null 2>&1; then
  echo "Told the board to check again — the banner clears in a few seconds."
fi
