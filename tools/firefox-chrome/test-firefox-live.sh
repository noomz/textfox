#!/usr/bin/env sh
set -eu

TOOL_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
export MOZ_HEADLESS=1
unset MOZ_REMOTE_ALLOW_SYSTEM_ACCESS

for state in on off; do
  result=$(sh "$TOOL_DIR/firefox-live.sh" --nova "$state" nav-bar)
  printf '%s\n' "$result" | python3 -c '
import json, sys
result = json.load(sys.stdin)
expected = sys.argv[1] == "on"
assert result["found"] and result["id"] == "nav-bar", result
assert result["novaEnabled"] is expected, result
' "$state"
done

if sh "$TOOL_DIR/firefox-live.sh" --nova invalid nav-bar >/dev/null 2>&1; then
  echo "invalid Nova state was accepted" >&2
  exit 1
fi

echo "Firefox live inspection: Nova on and off passed"
