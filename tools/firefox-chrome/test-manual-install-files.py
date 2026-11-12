#!/usr/bin/env python3
"""Drive tf-install.sh against a disposable profile directory.

What this covers, without a browser: what an install and a reinstall do to the
files a user owns. V2 requires that delivery preserves user configuration, so a
reinstall must not remove `chrome/config.css` and must not silently replace
`user.js`.

Exit 0 means every case below holds.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "tf-install.sh"
SENTINEL_CONFIG = ":root { --tf-border-width: 9px; }\n"
SENTINEL_USER_JS = 'user_pref("tf.test.sentinel", true);\n'


def install(profile: Path, answer: str) -> str:
    result = subprocess.run(
        ["bash", str(INSTALL), str(profile)],
        cwd=ROOT,
        input=f"{answer}\n",
        text=True,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"tf-install.sh failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def check_install(profile: Path) -> None:
    chrome = profile / "chrome"
    assert (chrome / "userChrome.css").is_file(), "install must ship userChrome.css"
    assert (chrome / "icons.css").is_file(), "install must ship the sheets userChrome.css imports"
    icons = sorted((chrome / "icons").glob("*.svg"))
    assert icons, "install must ship the icon set"
    assert (chrome / "defaults.css").is_file()
    assert not (chrome / "chrome").exists(), "install must not nest chrome/chrome on a reinstall"


def case_first_install(profile: Path) -> None:
    install(profile, "n")
    check_install(profile)
    assert not (profile / "chrome" / "config.css").exists(), (
        "install must not write config.css: it belongs to the user"
    )
    assert not (profile / "user.js").exists(), "declining user.js must install nothing"
    print("ok   fresh install ships the sheets, icons and no config.css")


def case_reinstall_keeps_user_config(profile: Path) -> None:
    install(profile, "n")
    config = profile / "chrome" / "config.css"
    config.write_text(SENTINEL_CONFIG)

    install(profile, "n")
    check_install(profile)
    assert config.is_file(), "a reinstall removed the user's chrome/config.css"
    assert config.read_text() == SENTINEL_CONFIG, "a reinstall rewrote chrome/config.css"

    backups = sorted(profile.glob("chrome-*.bak"))
    assert backups, "a reinstall must leave a backup of the files it replaced"
    assert (backups[0] / "userChrome.css").is_file(), "the backup must hold the replaced sheets"
    print("ok   reinstall keeps chrome/config.css and backs up the replaced sheets")


def case_reinstall_keeps_user_js(profile: Path) -> None:
    user_js = profile / "user.js"
    user_js.write_text(SENTINEL_USER_JS)
    install(profile, "y")
    check_install(profile)

    installed = (ROOT / "user.js").read_text()
    assert user_js.read_text() == installed, "answering yes must install the shipped user.js"
    backups = sorted(profile.glob("user.js-*.bak"))
    assert backups, "replacing an existing user.js must keep a backup of the old one"
    assert backups[0].read_text() == SENTINEL_USER_JS, "the user.js backup must hold the old bytes"
    print("ok   replacing an existing user.js keeps a backup of the old bytes")


def main() -> int:
    cases = [case_first_install, case_reinstall_keeps_user_config, case_reinstall_keeps_user_js]
    failures = 0
    for case in cases:
        profile = Path(tempfile.mkdtemp(prefix="tf-manual-"))
        try:
            case(profile)
        except AssertionError as error:
            failures += 1
            print(f"not ok {case.__name__}: {error}")
        finally:
            shutil.rmtree(profile, ignore_errors=True)
    if failures:
        print(f"\n{failures} of {len(cases)} manual install cases failed")
        return 1
    print(f"\nall {len(cases)} manual install cases hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
