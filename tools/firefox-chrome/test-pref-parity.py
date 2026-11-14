#!/usr/bin/env python3
"""Prove check-pref-parity.py fails on the drifts it exists to catch.

Each case writes one delivery artifact pair and expects a specific refusal. A case
that passes when it should fail means a delivery path can keep a different
preference state than the other without any check noticing.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "tools" / "firefox-chrome" / "check-pref-parity.py"

BASE_USER_JS = """\
// >>> textfox managed prefs >>>
user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);
user_pref("svg.context-properties.content.enabled", true);
user_pref("widget.macos.native-context-menus", false);
// <<< textfox managed prefs <<<
"""
BASE_WRAP = """\
    pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);
    pref("svg.context-properties.content.enabled", true);
    pref("widget.macos.native-context-menus", false);
"""


def run(user_js: str, wrap: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory(prefix="tf-pref-parity-") as directory:
        directory = Path(directory)
        (directory / "user.js").write_text(user_js)
        (directory / "wrap.nix").write_text(wrap)
        return subprocess.run([sys.executable, str(CHECK), str(directory / "user.js"),
                               str(directory / "wrap.nix")],
                              capture_output=True, text=True, timeout=60)


def expect_failure(name, user_js, wrap, needle):
    result = run(user_js, wrap)
    assert result.returncode != 0, f"{name}: drift accepted: {result.stdout}"
    assert needle in (result.stdout + result.stderr), (
        f"{name}: refusal does not name the cause: {result.stdout}{result.stderr}"
    )
    print(f"ok   {name}")


def main() -> int:
    passing = run(BASE_USER_JS, BASE_WRAP)
    assert passing.returncode == 0, f"aligned pair refused: {passing.stdout}{passing.stderr}"
    print("ok   aligned pair passes")

    expect_failure(
        "a preference missing from the autoconfig",
        BASE_USER_JS,
        BASE_WRAP.replace('    pref("svg.context-properties.content.enabled", true);\n', ""),
        "missing preferences",
    )
    expect_failure(
        "a preference missing from user.js",
        BASE_USER_JS.replace(
            'user_pref("svg.context-properties.content.enabled", true);\n', ""),
        BASE_WRAP,
        "missing preferences",
    )
    expect_failure(
        "the same preference with two values",
        BASE_USER_JS,
        BASE_WRAP.replace('pref("svg.context-properties.content.enabled", true);',
                          'pref("svg.context-properties.content.enabled", false);'),
        "preferences differ",
    )
    for value in ("false", "true"):
        expect_failure(
            f"the retired Nova gate assigned {value} in both files",
            BASE_USER_JS.replace("// <<<", f'user_pref("browser.nova.enabled", {value});\n// <<<'),
            BASE_WRAP + f'    pref("browser.nova.enabled", {value});\n',
            "the Nova gate is retired",
        )
    expect_failure(
        "native macOS context menus left on in both files",
        BASE_USER_JS.replace('"widget.macos.native-context-menus", false',
                             '"widget.macos.native-context-menus", true'),
        BASE_WRAP.replace('"widget.macos.native-context-menus", false',
                          '"widget.macos.native-context-menus", true'),
        "native context menus ignore userChrome.css",
    )
    expect_failure(
        "native macOS context menu preference dropped from both files",
        BASE_USER_JS.replace('user_pref("widget.macos.native-context-menus", false);\n', ""),
        BASE_WRAP.replace('    pref("widget.macos.native-context-menus", false);\n', ""),
        "native context menus ignore userChrome.css",
    )
    expect_failure(
        "a preference assigned twice in one artifact",
        BASE_USER_JS + 'user_pref("svg.context-properties.content.enabled", false);\n',
        BASE_WRAP,
        "more than once",
    )
    print("\nall preference-parity cases hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
