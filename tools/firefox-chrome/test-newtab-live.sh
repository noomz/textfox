#!/usr/bin/env sh
set -eu

TOOL_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
export MOZ_HEADLESS=1

for page in about:newtab about:home; do
for browser in on off; do
  for newtab in on off; do
    for theme in vanilla textfox; do
      if [ "$theme" = vanilla ]; then
        result=$(sh "$TOOL_DIR/firefox-live.sh" --vanilla --nova "$browser" --newtab-nova "$newtab" --newtab --newtab-page "$page" --wait-for .tile)
      else
        result=$(sh "$TOOL_DIR/firefox-live.sh" --nova "$browser" --newtab-nova "$newtab" --newtab --newtab-page "$page" --wait-for .tile)
      fi
      printf '%s\n' "$result" | python3 -c '
import json, sys
r = json.load(sys.stdin)
browser = sys.argv[1] == "on"
newtab = sys.argv[2] == "on"
theme = sys.argv[3]
page = sys.argv[4]
assert isinstance(r["defaultPreferences"]["browser.nova.enabled"], bool), r
assert isinstance(r["defaultPreferences"]["browser.newtabpage.activity-stream.nova.enabled"], bool), r
assert r["preferences"]["browser.nova.enabled"] is browser, r
assert r["preferences"]["browser.newtabpage.activity-stream.nova.enabled"] is newtab, r
assert r["url"] == page and r["initialized"], r
assert r["selectors"][".search-wrapper"] is not None, r
assert (r["selectors"][".nova-enabled"] is not None) is newtab, r
assert (r["selectors"][".classic-enabled"] is not None) is not newtab, r
if theme == "textfox":
    tile = r["selectors"][".tile"]
    assert tile is not None and tile["border"].startswith("2px solid"), r
    assert tile["border-radius"] == "0px" and tile["box-shadow"] == "none", r
' "$browser" "$newtab" "$theme" "$page"
    done
  done
done
done

echo "Firefox home and new-tab inspection: vanilla and textfox, all Nova preference combinations passed"
