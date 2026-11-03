#!/usr/bin/env sh
set -eu

TOOL_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
export MOZ_HEADLESS=1

for browser in on off; do
  for newtab in on off; do
    for theme in vanilla textfox; do
      if [ "$theme" = vanilla ]; then
        result=$(sh "$TOOL_DIR/firefox-live.sh" --vanilla --nova "$browser" --newtab-nova "$newtab" --newtab)
      else
        result=$(sh "$TOOL_DIR/firefox-live.sh" --nova "$browser" --newtab-nova "$newtab" --newtab)
      fi
      printf '%s\n' "$result" | python3 -c '
import json, sys
r = json.load(sys.stdin)
browser = sys.argv[1] == "on"
newtab = sys.argv[2] == "on"
assert isinstance(r["defaultPreferences"]["browser.nova.enabled"], bool), r
assert isinstance(r["defaultPreferences"]["browser.newtabpage.activity-stream.nova.enabled"], bool), r
assert r["preferences"]["browser.nova.enabled"] is browser, r
assert r["preferences"]["browser.newtabpage.activity-stream.nova.enabled"] is newtab, r
assert r["url"] == "about:newtab" and r["initialized"], r
assert r["selectors"][".search-wrapper"] is not None, r
assert (r["selectors"][".nova-enabled"] is not None) is newtab, r
assert (r["selectors"][".classic-enabled"] is not None) is not newtab, r
' "$browser" "$newtab"
    done
  done
done

echo "Firefox new-tab inspection: vanilla and textfox, all Nova preference combinations passed"
