#!/usr/bin/env python3
"""Compare the preferences textfox delivers through its two delivery artifacts.

V3 requires the intended preference state to be reached on every delivery path,
and the manual/home-manager paths read `user.js` while the NixOS autoconfig sets
`pref()` calls from `nix/pkgs/wrapTextfox.nix`. A preference present in one file
and missing or different in the other is a delivery path that silently keeps a
different state, and removing a disabled-Nova assignment from one file is not
proof that the other still disables it.

`browser.nova.enabled` must be absent from both files (T25). Any assignment pins
it as a user value: `false` disables Nova, and `true` is not Firefox's default.
The persisted `false` from earlier installs is removed once by
`tools/clear-nova-pref.py` (manual, home-manager) and by the autoconfig's
`clearUserPref` behind the `textfox.nova.cleared` marker (NixOS), neither of
which this check parses.

`widget.macos.native-context-menus` is pinned false as well (§B B9, V10): when it
is true, macOS draws the page context menu itself and textfox's menu CSS never
paints, while every headless check still sees a styled XUL popup.

Exit 0 means both files name the same preferences with the same values.
"""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
USER_JS = ROOT / "user.js"
WRAP = ROOT / "nix" / "pkgs" / "wrapTextfox.nix"
NOVA = "browser.nova.enabled"
NATIVE_MENUS = "widget.macos.native-context-menus"

# `user_pref("name", value);` and `pref("name", value);`. Values are what the two
# artifacts can carry: booleans, integers, and double-quoted strings.
USER_PREF = re.compile(r'^\s*user_pref\(\s*"([^"]+)"\s*,\s*([^)]+?)\s*\)\s*;', re.MULTILINE)
NIX_PREF = re.compile(r'^\s*pref\(\s*"([^"]+)"\s*,\s*([^)]+?)\s*\)\s*;', re.MULTILINE)


def pref_map(text, pattern, where):
    """Parse `name -> value` and refuse duplicate or unparseable assignments."""
    found = {}
    for name, raw in pattern.findall(text):
        if name in found:
            raise ValueError(f"{where} assigns {name} more than once")
        found[name] = json.loads(raw)
    return found


def compare(user_js, wrap):
    delivered = pref_map(user_js.read_text(), USER_PREF, str(user_js))
    generated = pref_map(wrap.read_text(), NIX_PREF, str(wrap))

    missing = sorted(set(delivered) - set(generated))
    extra = sorted(set(generated) - set(delivered))
    if missing:
        raise ValueError(f"{wrap} is missing preferences from {user_js}: {missing}")
    if extra:
        raise ValueError(f"{user_js} is missing preferences from {wrap}: {extra}")

    differing = {name: (delivered[name], generated[name])
                 for name in delivered if delivered[name] != generated[name]}
    if differing:
        raise ValueError(f"preferences differ between the delivery artifacts: {differing}")

    if NOVA in delivered:
        raise ValueError(
            f"{NOVA} is assigned {delivered[NOVA]!r}; the Nova gate is retired (T25), and any "
            "assignment pins it instead of leaving Firefox's default"
        )
    if delivered.get(NATIVE_MENUS) is not False:
        raise ValueError(
            f"{NATIVE_MENUS} is {delivered.get(NATIVE_MENUS)!r}; macOS native context "
            "menus ignore userChrome.css, so both artifacts must set it to false (B9)"
        )
    return delivered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user_js", nargs="?", type=Path, default=USER_JS)
    parser.add_argument("wrap_textfox", nargs="?", type=Path, default=WRAP)
    args = parser.parse_args()

    try:
        preferences = compare(args.user_js, args.wrap_textfox)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"not ok {error}")
        return 1
    names = ", ".join(sorted(preferences))
    print(f"ok   both delivery artifacts set the same preferences ({len(preferences)}): {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
