#!/bin/sh
# Reconcile named JSONL probe outputs in a directory; see NOVA-PORT.md for probe options.
set -eu
base=${1:?usage: reconcile-selector-evidence.sh OUTPUT_DIRECTORY}
tool=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/selector-evidence.py
exec python3 "$tool" "$base/textfox-t2-expanded-final.json" \
  "default-dom=$base/textfox-t2-selector-dom-final.jsonl" \
  "dynamic-dom=$base/textfox-t2-selector-dynamic-all.jsonl" \
  "app-subviews=$base/textfox-t2-app-subviews-selectors-final.jsonl" \
  "findbar=$base/textfox-t2-findbar-selectors-final.jsonl" \
  "identity-secure=$base/textfox-t2-identity-selectors-secure-final.jsonl" \
  "identity-mixed=$base/textfox-t2-identity-selectors-mixed-final.jsonl" \
  "builtin-menu=$base/textfox-t2-menu-selectors-builtin.jsonl" \
  "menu-overflow=$base/textfox-t2-menu-selectors-overflow.jsonl" \
  "newtab-cards=$base/textfox-t2-newtab-card-selectors.jsonl" \
  "newtab-hover=$base/textfox-t2-newtab-hover-selectors.jsonl" \
  "newtab-menu=$base/textfox-t2-newtab-menu-selectors-final.jsonl" \
  "newtab-search=$base/textfox-t2-newtab-search-selectors.jsonl" \
  "newtab-wallpaper=$base/textfox-t2-newtab-wallpaper-selectors.jsonl" \
  "identity-colors=$base/textfox-t2-overwrites-selectors-final.jsonl" \
  "panel=$base/textfox-t2-panel-selectors-final.jsonl" \
  "sidebar-shadow=$base/textfox-t2-sidebar-shadow-selectors.jsonl" \
  "split-hover=$base/textfox-t2-split-hover-selectors.jsonl" \
  "split-fullscreen=$base/textfox-t2-split-selector-fullscreen.jsonl" \
  "tabs-horizontal=$base/textfox-t2-tabs-selectors-horizontal.jsonl" \
  "tabs-vertical=$base/textfox-t2-tabs-selectors-vertical.jsonl" \
  "unified-menu=$base/textfox-t2-unified-selectors-final.jsonl" \
  "downloads-menu=$base/textfox-t2-download-selector-final.jsonl" \
  "extension-toolbar=$base/textfox-t2-extension-toolbar-selectors-final.jsonl" \
  "extension-context=$base/textfox-t2-extension-context-selectors-final.jsonl" \
  "places-toolbar=$base/textfox-t2-places-selectors-final.jsonl" \
  "bookmarks-menu=$base/textfox-t2-bookmarks-menu-selectors-final.jsonl"
