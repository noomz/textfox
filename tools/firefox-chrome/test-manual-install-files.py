#!/usr/bin/env python3
"""Drive tf-install.sh and tf-uninstall.sh against a disposable profile directory.

What this covers, without a browser: what an install, a reinstall, and an
uninstall do to the files a user owns. V2 requires that delivery preserves user
configuration, so a reinstall must not remove `chrome/config.css`, and the
preference block textfox merges into `user.js` must leave the user's own
preferences outside it intact — including when the profile already had a
textfox block from an older install.

Exit 0 means every case below holds.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "tf-install.sh"
UNINSTALL = ROOT / "tf-uninstall.sh"
# The shell that runs the scripts. Set TF_TEST_SHELL=sh to run them the way the
# README does, for example under macOS /bin/sh (bash 3.2 in POSIX mode).
SHELL = os.environ.get("TF_TEST_SHELL", "bash")
SENTINEL_CONFIG = ":root { --tf-border-width: 9px; }\n"
SENTINEL_USER_JS = 'user_pref("tf.test.sentinel", true);\n'
BEGIN = "// >>> textfox managed prefs >>>"
END = "// <<< textfox managed prefs <<<"
LOGO = r"\__/\___/_/|_|\__/_/  \____/_/|_|"


def run(script: Path, profile: Path, answer: str = "") -> str:
    result = subprocess.run(
        [SHELL, str(script), str(profile)],
        cwd=ROOT,
        input=answer,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{script.name} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def install(profile: Path) -> str:
    return run(INSTALL, profile)


def uninstall(profile: Path) -> str:
    # The uninstaller only prompts when it finds a chrome backup to restore.
    return run(UNINSTALL, profile, "n\n")


def check_install(profile: Path) -> None:
    chrome = profile / "chrome"
    assert (chrome / "userChrome.css").is_file(), "install must ship userChrome.css"
    assert (chrome / "icons.css").is_file(), "install must ship the sheets userChrome.css imports"
    icons = sorted((chrome / "icons").glob("*.svg"))
    assert icons, "install must ship the icon set"
    assert (chrome / "defaults.css").is_file()
    assert not (chrome / "chrome").exists(), "install must not nest chrome/chrome on a reinstall"


def shipped_prefs() -> list:
    """The preference lines the repo's user.js delivers between its markers."""
    return [line for line in (ROOT / "user.js").read_text().splitlines()
            if line.startswith("user_pref(")]


def check_pref_block(profile: Path) -> str:
    text = (profile / "user.js").read_text()
    assert text.count(BEGIN) == 1, f"install must write exactly one managed block:\n{text}"
    assert text.count(END) == 1, f"install must close the managed block:\n{text}"
    for line in shipped_prefs():
        assert line in text, f"managed block is missing {line}"
    return text


def case_fresh_install(profile: Path) -> None:
    install(profile)
    check_install(profile)
    assert not (profile / "chrome" / "config.css").exists(), (
        "install must not write config.css: it belongs to the user"
    )
    check_pref_block(profile)
    print("ok   fresh install ships the sheets, icons, and the managed pref block")


def case_reinstall_keeps_user_config(profile: Path) -> None:
    install(profile)
    config = profile / "chrome" / "config.css"
    config.write_text(SENTINEL_CONFIG)

    install(profile)
    check_install(profile)
    assert config.is_file(), "a reinstall removed the user's chrome/config.css"
    assert config.read_text() == SENTINEL_CONFIG, "a reinstall rewrote chrome/config.css"

    backups = sorted(profile.glob("chrome-*.bak"))
    assert backups, "a reinstall must leave a backup of the files it replaced"
    assert (backups[0] / "userChrome.css").is_file(), "the backup must hold the replaced sheets"
    print("ok   reinstall keeps chrome/config.css and backs up the replaced sheets")


def case_merge_keeps_user_prefs(profile: Path) -> None:
    user_js = profile / "user.js"
    user_js.write_text("// my settings\n" + SENTINEL_USER_JS)
    install(profile)

    text = check_pref_block(profile)
    assert "browser.nova.enabled" not in text, "merge must not assign the retired Nova gate (T25)"
    assert SENTINEL_USER_JS.strip() in text, "merge removed the user's own preference"
    assert text.startswith("// my settings\n"), "merge moved the user's own text"
    backups = sorted(profile.glob("user.js-*.bak"))
    assert backups, "changing an existing user.js must keep a backup"
    assert backups[0].read_text() == "// my settings\n" + SENTINEL_USER_JS, (
        "the backup must hold the bytes the merge replaced"
    )
    print("ok   merge keeps the user's own preferences and backs up the old file")


def case_reinstall_is_idempotent(profile: Path) -> None:
    user_js = profile / "user.js"
    user_js.write_text(SENTINEL_USER_JS)
    install(profile)
    first = user_js.read_text()
    backups = len(list(profile.glob("user.js-*.bak")))

    install(profile)
    assert user_js.read_text() == first, "a reinstall rewrote an already-current user.js"
    assert len(list(profile.glob("user.js-*.bak"))) == backups, (
        "a reinstall that changes nothing must not create another backup"
    )
    print("ok   reinstalling an unchanged profile writes nothing")


def case_replaces_stale_block(profile: Path) -> None:
    user_js = profile / "user.js"
    user_js.write_text(
        SENTINEL_USER_JS
        + "\n"
        + BEGIN
        + "\n"
        + 'user_pref("browser.nova.enabled", true);\n'
        + END
        + "\n"
    )
    install(profile)

    text = check_pref_block(profile)
    assert 'user_pref("browser.nova.enabled", true);' not in text, (
        "a stale managed block kept its old value"
    )
    assert "browser.nova.enabled" not in text, "the retired Nova gate is still assigned (T25)"
    assert SENTINEL_USER_JS.strip() in text, "replacing a stale block removed the user's preference"
    print("ok   a stale managed block is replaced, not duplicated")


def case_unbalanced_markers_refused(profile: Path) -> None:
    user_js = profile / "user.js"
    broken = SENTINEL_USER_JS + BEGIN + "\n"
    user_js.write_text(broken)
    result = subprocess.run(
        [SHELL, str(INSTALL), str(profile)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode != 0, "install must refuse a user.js with an unclosed marker"
    assert user_js.read_text() == broken, "a refused install changed user.js anyway"
    print("ok   an unclosed marker is refused without touching the file")


def case_uninstall_keeps_user_prefs(profile: Path) -> None:
    user_js = profile / "user.js"
    user_js.write_text("// mine\n" + SENTINEL_USER_JS)
    install(profile)

    uninstall(profile)
    text = user_js.read_text()
    assert BEGIN not in text and END not in text, "uninstall left the managed block behind"
    assert 'user_pref("browser.nova.enabled", false);' not in text, "uninstall left textfox's prefs"
    assert "// mine" in text and SENTINEL_USER_JS.strip() in text, (
        "uninstall removed the user's own preferences"
    )
    assert sorted(profile.glob("user.js-*.bak")), "uninstall must keep the replaced bytes"
    print("ok   uninstall removes only textfox's block")


def case_uninstall_removes_own_file(profile: Path) -> None:
    install(profile)
    uninstall(profile)
    assert not (profile / "user.js").exists(), (
        "a user.js that held only textfox's block must not stay behind"
    )
    print("ok   uninstall removes a user.js it created")


def case_uninstall_leaves_foreign_file(profile: Path) -> None:
    user_js = profile / "user.js"
    user_js.write_text(SENTINEL_USER_JS)
    uninstall(profile)
    assert user_js.read_text() == SENTINEL_USER_JS, "uninstall changed a user.js textfox never wrote"
    print("ok   uninstall leaves a user.js without a managed block untouched")


def shipped_files() -> list:
    """Paths under chrome/ that the repo ships, relative to chrome/."""
    source = ROOT / "chrome"
    return sorted(path.relative_to(source) for path in source.rglob("*") if path.is_file())


def case_uninstall_keeps_user_config(profile: Path) -> None:
    install(profile)
    chrome = profile / "chrome"
    config = chrome / "config.css"
    config.write_text(SENTINEL_CONFIG)
    own_icon = chrome / "icons" / "my-own.svg"
    own_icon.write_text("<svg/>\n")

    uninstall(profile)
    assert config.is_file(), "uninstall removed the user's chrome/config.css"
    assert config.read_text() == SENTINEL_CONFIG, "uninstall rewrote chrome/config.css"
    assert own_icon.read_text() == "<svg/>\n", "uninstall removed a file the user added under icons/"
    left = [str(rel) for rel in shipped_files() if (chrome / rel).exists()]
    assert not left, f"uninstall left textfox's own files behind: {left[:5]}"
    print("ok   uninstall removes textfox's files and keeps config.css and the user's own files")


def case_uninstall_removes_emptied_chrome(profile: Path) -> None:
    install(profile)
    uninstall(profile)
    assert not (profile / "chrome").exists(), (
        "a chrome directory that held only textfox's files must not stay behind"
    )
    print("ok   uninstall removes a chrome directory left empty")


def case_uninstall_restores_backup_beside_config(profile: Path) -> None:
    chrome = profile / "chrome"
    chrome.mkdir()
    mine = "/* the user's own userChrome.css */\n"
    (chrome / "userChrome.css").write_text(mine)
    (chrome / "config.css").write_text(SENTINEL_CONFIG)
    install(profile)
    assert sorted(profile.glob("chrome-*.bak")), "install must back up the replaced userChrome.css"

    run(UNINSTALL, profile, "y\n")
    assert (chrome / "userChrome.css").read_text() == mine, (
        "restoring the backup did not bring back the user's userChrome.css"
    )
    assert (chrome / "config.css").read_text() == SENTINEL_CONFIG, (
        "restoring the backup touched chrome/config.css"
    )
    assert not list(chrome.glob("chrome-*.bak")), "the backup was nested inside chrome/"
    assert not sorted(profile.glob("chrome-*.bak")), "a fully restored backup must not stay behind"
    print("ok   uninstall restores a backup beside the kept config.css")


def case_reversed_markers_refused(profile: Path) -> None:
    user_js = profile / "user.js"
    broken = END + "\n" + SENTINEL_USER_JS + BEGIN + "\n" + 'user_pref("tf.test.after", 1);\n'
    for script in (INSTALL, UNINSTALL):
        user_js.write_text(broken)
        result = subprocess.run(
            [SHELL, str(script), str(profile)],
            cwd=ROOT, input="n\n", text=True, capture_output=True, timeout=120,
        )
        assert user_js.read_text() == broken, f"{script.name} changed a user.js with reversed markers"
        assert result.returncode != 0 or script == UNINSTALL, f"{script.name} accepted reversed markers"
    print("ok   reversed markers are refused by install and uninstall")


def case_uninstall_keeps_config_shipped_in_checkout(profile: Path) -> None:
    # A user may keep their config.css in the checkout's chrome/ (it is
    # gitignored there). Uninstall must still not treat it as textfox's.
    checkout = Path(tempfile.mkdtemp(prefix="tf-checkout-"))
    try:
        shutil.copytree(ROOT / "chrome", checkout / "chrome")
        shutil.copy2(UNINSTALL, checkout / UNINSTALL.name)
        (checkout / "chrome" / "config.css").write_text(":root {}\n")
        install(profile)
        config = profile / "chrome" / "config.css"
        config.write_text(SENTINEL_CONFIG)
        run(checkout / UNINSTALL.name, profile, "n\n")
        assert config.read_text() == SENTINEL_CONFIG, (
            "uninstall removed config.css because the checkout held one"
        )
    finally:
        shutil.rmtree(checkout)
    print("ok   uninstall keeps config.css even when the checkout holds one")


def restore_answer_output(profile: Path) -> str:
    """Uninstall, accepting whatever backup the uninstaller offers."""
    return run(UNINSTALL, profile, "y\n")


def case_uninstall_offers_pre_textfox_backup(profile: Path) -> None:
    # A reinstall backs up the previous textfox files. The backup to offer is
    # still the one holding the user's files from before textfox.
    chrome = profile / "chrome"
    chrome.mkdir()
    mine = "/* the user's own userChrome.css */\n"
    (chrome / "userChrome.css").write_text(mine)
    install(profile)
    install(profile)
    backups = sorted(profile.glob("chrome-*.bak"))
    assert len(backups) == 2, f"two installs must leave two backups, got {backups}"

    restore_answer_output(profile)
    assert (chrome / "userChrome.css").read_text() == mine, (
        "uninstall restored textfox's previous files instead of the user's pre-textfox backup"
    )
    left = sorted(profile.glob("chrome-*.bak"))
    assert len(left) == 1 and (left[0] / "userChrome.css").is_file(), (
        "the backup of textfox's previous files must stay, unrestored"
    )
    print("ok   uninstall offers the pre-textfox backup, not the newest one")


def case_uninstall_never_restores_textfox_backup(profile: Path) -> None:
    install(profile)
    install(profile)
    assert sorted(profile.glob("chrome-*.bak")), "a reinstall must back up textfox's previous files"
    restore_answer_output(profile)
    assert not (profile / "chrome").exists(), (
        "uninstall put textfox's previous files back after removing textfox"
    )
    print("ok   uninstall never offers a backup of textfox's own files")


def case_install_recognises_unmarked_textfox(profile: Path) -> None:
    # Installs from before the user.js markers left no marker. Their sheets are
    # still textfox's, so a reinstall must not label them as the user's.
    shutil.copytree(ROOT / "chrome", profile / "chrome")
    legacy_old = profile / "chrome-20250101_000000.bak"
    legacy_new = profile / "chrome-20250102_000000.bak"
    for directory, body in ((legacy_old, "/* oldest */\n"), (legacy_new, "/* newer */\n")):
        directory.mkdir()
        (directory / "userChrome.css").write_text(body)
    install(profile)
    restore_answer_output(profile)
    assert (profile / "chrome" / "userChrome.css").read_text() == "/* oldest */\n", (
        "with only legacy backups, uninstall must offer the oldest one"
    )
    assert legacy_new.is_dir(), "the newer legacy backup must stay"
    print("ok   unmarked textfox installs are recognised; the oldest legacy backup is offered")


def case_user_sheet_over_textfox_is_offered(profile: Path) -> None:
    # The user replaced textfox's userChrome.css with their own after
    # installing. The profile still says textfox is installed, but that one file
    # is the user's, and uninstall must be able to offer it back.
    install(profile)
    mine = "/* written over textfox */\n"
    (profile / "chrome" / "userChrome.css").write_text(mine)
    install(profile)
    pre = sorted(profile.glob("chrome-pre-textfox-*.bak"))
    assert len(pre) == 1 and (pre[0] / "userChrome.css").read_text() == mine, (
        "the user's own userChrome.css was not backed up as theirs"
    )
    assert not (pre[0] / "icons.css").exists(), "textfox's files landed in the user's backup"
    restore_answer_output(profile)
    assert (profile / "chrome" / "userChrome.css").read_text() == mine
    print("ok   a sheet the user wrote over textfox is backed up as theirs and offered")


def case_same_second_backups_stay_apart(profile: Path) -> None:
    import datetime
    install(profile)
    now = datetime.datetime.now()
    taken = []
    for offset in range(0, 6):
        stamp = (now + datetime.timedelta(seconds=offset)).strftime("%Y%m%d_%H%M%S")
        taken.append(profile / f"chrome-textfox-{stamp}.bak")
    for directory in taken:
        directory.mkdir()
        (directory / "sentinel").write_text(directory.name)
    install(profile)
    for directory in taken:
        assert sorted(p.name for p in directory.iterdir()) == ["sentinel"], (
            f"a reinstall merged into the existing backup {directory.name}"
        )
    suffixed = [p for p in profile.glob("chrome-textfox-*_*.bak") if p.name.count("_") == 2]
    assert len(suffixed) == 1, f"expected one suffixed backup, got {suffixed}"
    print("ok   two backups in one second stay apart")


# T25: an earlier textfox install left browser.nova.enabled false in prefs.js.
OLD_BLOCK = (ROOT / "tools" / "firefox-chrome" / "fixtures" / "user.js-nova-false").read_text()
NOVA_FALSE = 'user_pref("browser.nova.enabled", false);\n'
MARKER = 'user_pref("textfox.nova.cleared", true);\n'
PREFS_SENTINEL = 'user_pref("tf.prefs.sentinel", 7);\n'


def old_textfox_profile(profile: Path) -> None:
    """A profile a pre-T25 textfox was installed in and Firefox then ran."""
    (profile / "user.js").write_text(SENTINEL_USER_JS + "\n" + OLD_BLOCK)
    (profile / "prefs.js").write_text("// Mozilla User Preferences\n" + PREFS_SENTINEL + NOVA_FALSE)


def run_raw(profile: Path, env=None) -> subprocess.CompletedProcess:
    return subprocess.run([SHELL, str(INSTALL), str(profile)], cwd=ROOT, text=True,
                          capture_output=True, timeout=120, env=env)


def case_install_clears_old_nova_false(profile: Path) -> None:
    old_textfox_profile(profile)
    install(profile)
    text = check_pref_block(profile)
    assert "browser.nova.enabled" not in text, f"the retired block still assigns Nova:\n{text}"
    assert SENTINEL_USER_JS in text, "the user's own user.js line was lost"
    prefs = (profile / "prefs.js").read_text()
    assert NOVA_FALSE not in prefs, f"the persisted Nova false survived the install:\n{prefs}"
    assert MARKER in prefs and PREFS_SENTINEL in prefs, f"marker or user pref missing:\n{prefs}"
    install(profile)
    assert (profile / "prefs.js").read_text() == prefs, "a reinstall changed prefs.js again"
    print("ok   reinstall over an old textfox clears the persisted Nova false once")


def case_install_aborts_while_firefox_holds_profile(profile: Path) -> None:
    old_textfox_profile(profile)
    (profile / "chrome").mkdir()
    (profile / "chrome" / "userChrome.css").write_text("/* old textfox */\n" + LOGO + "\n")
    before = {p.relative_to(profile): p.read_bytes() for p in profile.rglob("*") if p.is_file()}
    holder = subprocess.Popen([sys.executable, "-c",
                               "import fcntl, os, sys, time\n"
                               "fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT, 0o644)\n"
                               "fcntl.lockf(fd, fcntl.LOCK_EX)\n"
                               "print('held', flush=True)\n"
                               "time.sleep(60)\n", str(profile / ".parentlock")],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        result = run_raw(profile)
    finally:
        holder.kill()
        holder.wait()
    assert result.returncode != 0, "install must refuse while Firefox holds the profile"
    assert "close Firefox" in result.stdout + result.stderr, result.stdout + result.stderr
    after = {p.relative_to(profile): p.read_bytes() for p in profile.rglob("*") if p.is_file()}
    after.pop(Path(".parentlock"), None)
    before.pop(Path(".parentlock"), None)
    assert after == before, "a refused install changed the profile"
    print("ok   install aborts untouched while Firefox holds the profile")


def case_install_keeps_nova_on_unrelated_profile(profile: Path) -> None:
    (profile / "prefs.js").write_text(PREFS_SENTINEL + NOVA_FALSE)
    install(profile)
    prefs = (profile / "prefs.js").read_text()
    assert prefs == PREFS_SENTINEL + NOVA_FALSE, f"a first install touched the user's prefs.js:\n{prefs}"
    print("ok   a first install leaves a Nova value it never wrote alone")


def case_install_without_python_prints_manual_step(profile: Path) -> None:
    old_textfox_profile(profile)
    bin_dir = Path(tempfile.mkdtemp(prefix="tf-nopython-"))
    try:
        for directory in os.environ["PATH"].split(os.pathsep):
            if not os.path.isdir(directory):
                continue
            for name in os.listdir(directory):
                target = bin_dir / name
                if name.startswith("python") or target.exists():
                    continue
                source = Path(directory) / name
                if source.is_file() and os.access(source, os.X_OK):
                    target.symlink_to(source)
        result = run_raw(profile, env={**os.environ, "PATH": str(bin_dir)})
    finally:
        shutil.rmtree(bin_dir, ignore_errors=True)
    assert result.returncode == 0, f"install without python3 failed:\n{result.stdout}\n{result.stderr}"
    assert "reset browser.nova.enabled" in result.stdout, result.stdout
    assert NOVA_FALSE in (profile / "prefs.js").read_text(), "prefs.js changed without the tool"
    assert "browser.nova.enabled" not in (profile / "user.js").read_text()
    print("ok   without python3 the install completes and names the about:config step")


def main() -> int:
    cases = [
        case_fresh_install,
        case_reinstall_keeps_user_config,
        case_merge_keeps_user_prefs,
        case_reinstall_is_idempotent,
        case_replaces_stale_block,
        case_unbalanced_markers_refused,
        case_uninstall_keeps_user_prefs,
        case_uninstall_removes_own_file,
        case_uninstall_leaves_foreign_file,
        case_uninstall_keeps_user_config,
        case_uninstall_removes_emptied_chrome,
        case_uninstall_restores_backup_beside_config,
        case_reversed_markers_refused,
        case_uninstall_keeps_config_shipped_in_checkout,
        case_uninstall_offers_pre_textfox_backup,
        case_uninstall_never_restores_textfox_backup,
        case_install_recognises_unmarked_textfox,
        case_user_sheet_over_textfox_is_offered,
        case_same_second_backups_stay_apart,
        case_install_clears_old_nova_false,
        case_install_aborts_while_firefox_holds_profile,
        case_install_keeps_nova_on_unrelated_profile,
        case_install_without_python_prints_manual_step,
    ]
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
