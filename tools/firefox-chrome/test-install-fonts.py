#!/usr/bin/env python3
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SHELL = os.environ.get("TF_TEST_SHELL", "/bin/bash")


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tf-font-install-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.checkout = self.base / "checkout"
        self.checkout.mkdir()
        shutil.copy2(ROOT / "tf-install.sh", self.checkout)
        shutil.copy2(ROOT / "user.js", self.checkout)
        shutil.copytree(ROOT / "chrome", self.checkout / "chrome")
        shutil.copytree(ROOT / "tools", self.checkout / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        self.profile = self.base / "profile with spaces"
        self.profile.mkdir()
        self.config = self.profile / "chrome/config.css"

    def run_install(self, *args, env=None, input=None):
        if input is None:
            input = str(self.profile) + "\n"
        return subprocess.run([SHELL, "tf-install.sh", *map(str, args)],
                              cwd=self.checkout, input=input, text=True,
                              capture_output=True, timeout=30, env=env)

    def shim(self, command, body):
        directory = self.base / "bin"
        directory.mkdir(exist_ok=True)
        target = directory / command
        target.write_text("#!/bin/bash\n" + body + "\n")
        target.chmod(0o755)
        return {**os.environ, "PATH": str(directory) + os.pathsep + os.environ["PATH"]}

    def assert_failed(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Installation completed", result.stdout)

    def snapshot(self):
        return {str(p.relative_to(self.profile)): (p.lstat().st_mode,
                    p.lstat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
                for p in self.profile.rglob("*")}

    def test_help(self):
        result = self.run_install("--help")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--font-family", result.stdout)
        self.assertEqual(self.snapshot(), {})

    def test_invalid_cli_is_preflighted(self):
        cases = [("--wat",), ("--profile",), ("--font-size",),
                 ("--font-family", ""), ("--font-size", ""),
                 ("--profile", ""), ("--profile", self.profile, "--profile", self.profile),
                 ("--font-size", "12px", "--font-size", "13px"),
                 ("--font-family", "serif", "--font-family", "monospace"),
                 (self.profile, "other"), ("--profile", self.profile, self.profile),
                 ("--help", self.profile)]
        for args in cases:
            with self.subTest(args=args):
                before = self.snapshot()
                result = self.run_install(*args)
                self.assert_failed(result)
                self.assertIn("Usage: bash tf-install.sh", result.stderr)
                self.assertNotIn("Path to Firefox profile", result.stdout)
                self.assertEqual(before, self.snapshot())

    def test_named_positional_interactive_and_sentinel(self):
        for args, input in [((), str(self.profile) + "\n"),
                            (("--profile", self.profile), ""),
                            (("--", self.profile), ""), ((self.profile,), "")]:
            result = self.run_install(*args, input=input)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((self.profile / "chrome/userChrome.css").exists())

    def test_legacy_missing_positional_path_reprompts(self):
        result = self.run_install(self.base / "missing", input=str(self.profile) + "\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.profile / "chrome/userChrome.css").is_file())

    def test_sentinel_allows_a_path_starting_with_dash(self):
        profile = self.checkout / "--font-size"
        profile.mkdir()
        result = self.run_install("--", "--font-size")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((profile / "chrome/userChrome.css").is_file())

    def test_missing_directory_and_eof_do_not_loop(self):
        self.assert_failed(self.run_install("--profile", self.base / "absent"))
        self.assert_failed(self.run_install(input=""))

    def test_font_creation_partial_and_noop(self):
        (self.checkout / "chrome/config.css").write_bytes(b"ignored checkout config")
        result = self.run_install("--font-family", '"JetBrains Mono", monospace',
                                  "--profile", self.profile, "--font-size", "13px")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.config.exists(), "font flags must create a managed config block")
        self.assertEqual(self.config.read_bytes(),
                         b'/* >>> textfox managed fonts >>> */\n:root {\n'
                         b'  --tf-font-family: "JetBrains Mono", monospace;\n'
                         b'  --tf-font-size: 13px;\n}\n/* <<< textfox managed fonts <<< */\n')
        original = self.config.read_bytes()
        self.config.chmod(0o640)
        before = self.config.stat().st_mtime_ns
        result = self.run_install(self.profile, "--font-size", "13px")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.config.stat().st_mtime_ns, before)
        self.assertFalse(list(self.config.parent.glob("config.css-*.bak")))
        result = self.run_install(self.profile, "--font-size", "15px")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b'--tf-font-family: "JetBrains Mono", monospace;', self.config.read_bytes())
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.assertEqual([p.read_bytes() for p in self.config.parent.glob("config.css-*.bak")], [original])

    def test_only_requested_value_is_written(self):
        result = self.run_install(self.profile, "--font-size", ".9rem")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(b"--tf-font-family", self.config.read_bytes())

    def test_outside_bytes_preserved(self):
        bodies = [b"", b"\xef\xbb\xbf", b":root { --mine: 7; }", b"/* caf\xc3\xa9 */\r\n\r\n",
                  b"\xef\xbb\xbf:root { --mine: 9; }\r\n\r\n", b"\n\n/* trailing */\n\n"]
        for original in bodies:
            with self.subTest(original=original):
                self.config.parent.mkdir(exist_ok=True)
                self.config.write_bytes(original)
                result = self.run_install(self.profile, "--font-size", "12px")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                expected_suffix = original[3:] if original.startswith(b"\xef\xbb\xbf") else original
                data = self.config.read_bytes()
                bom = b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b""
                self.assertTrue(data.startswith(bom + b"/* >>> textfox managed fonts >>> */\n"))
                self.assertTrue(data.endswith(b"/* <<< textfox managed fonts <<< */\n" + expected_suffix))
                self.assertIn(original, [p.read_bytes() for p in self.config.parent.glob("config.css-*.bak")])

    def test_bad_values_leave_entire_profile_untouched(self):
        cases = [("--font-size", v) for v in ["0px", "-2px", "12", "1e2px", "12px!important", "2px;}", "NaNpx", "1px\n"]]
        cases += [("--font-family", v) for v in ["serif; color:red", "var(--font)", "serif/*x*/", "serif!important", '"bad', "serif,", "a\\b", "serif\n", "inherit", "initial", "revert-layer", "serif unset", '"textfox managed fonts"']]
        for flag, value in cases:
            with self.subTest(value=value):
                before = self.snapshot()
                result = self.run_install(self.profile, flag, value)
                self.assert_failed(result)
                self.assertEqual(before, self.snapshot())

    def test_unsafe_config_is_preflighted(self):
        begin = b"/* >>> textfox managed fonts >>> */\n"
        end = b"/* <<< textfox managed fonts <<< */\n"
        good = begin + b":root {\n  --tf-font-size: 12px;\n}\n" + end
        bodies = [b'@charset "UTF-8";', b'@IMPORT "file.css";', b'@namespace url(x);',
                  b'@layer x {}', b'@im\\port "x";', b'@\\69mport "x";', b'/* unfinished',
                  b'"unfinished', b'\xff', b'\xff\xfea\0', b'a\0b', begin, end + begin,
                  good + good, b"/* displaced */\n" + good,
                  good.replace(b"12px", b"12px !important"),
                  good.replace(b":root", b"body"), good.replace(b"12px", b"-2px"),
                  begin + begin + end + end]
        self.config.parent.mkdir()
        for original in bodies:
            with self.subTest(original=original):
                self.config.write_bytes(original)
                before = self.snapshot()
                result = self.run_install(self.profile, "--font-size", "14px")
                self.assert_failed(result)
                self.assertIn("without font flags", result.stderr)
                self.assertEqual(before, self.snapshot())
        result = self.run_install(self.profile)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.config.read_bytes(), bodies[-1])

    def test_comments_strings_and_ordinary_at_rules_are_allowed(self):
        self.config.parent.mkdir()
        original = (b'/* @import @layer */\r\n:root { --note: "@namespace"; }\n'
                    b'@media (width > 4px) { :root { --x: 1; } }\n@supports (display: grid) {}')
        self.config.write_bytes(original)
        result = self.run_install(self.profile, "--font-family", '"Noto Sans \u0e44\u0e17\u0e22", sans-serif')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.config.read_bytes().endswith(original))

    def test_symlink_config_and_chrome_are_refused(self):
        outside = self.base / "outside"
        outside.write_bytes(b"outside")
        self.config.parent.mkdir()
        self.config.symlink_to(outside)
        result = self.run_install(self.profile, "--font-size", "12px")
        self.assert_failed(result)
        self.assertEqual(outside.read_bytes(), b"outside")
        self.assertEqual(sorted(p.name for p in self.config.parent.iterdir()), ["config.css"])
        self.config.unlink()
        self.config.parent.rmdir()
        directory = self.base / "outside-dir"
        directory.mkdir()
        self.config.parent.symlink_to(directory, target_is_directory=True)
        self.assert_failed(self.run_install(self.profile, "--font-size", "12px"))
        self.assertEqual(list(directory.iterdir()), [])

    def test_font_callback_failure_does_not_write_config(self):
        self.config.parent.mkdir()
        self.config.write_bytes(b"/* mine */")
        result = self.run_install(self.profile, "--font-size", "12px", env=self.shim("cp", "exit 62"))
        self.assert_failed(result)
        self.assertEqual(self.config.read_bytes(), b"/* mine */")
        self.assertFalse(list(self.config.parent.glob("config.css-*")))

    def test_python_required_only_with_fonts(self):
        env = self.shim("python3", "exit 1")
        before = self.snapshot()
        self.assert_failed(self.run_install(self.profile, "--font-size", "12px", env=env))
        self.assertEqual(before, self.snapshot())
        result = self.run_install(self.profile, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fonts_refused_when_python_is_absent(self):
        directory = self.base / "empty-path"
        directory.mkdir()
        result = self.run_install(self.profile, "--font-size", "12px",
                                  env={**os.environ, "PATH": str(directory)})
        self.assert_failed(result)
        self.assertIn("Python 3.8", result.stderr)
        self.assertEqual(self.snapshot(), {})

    def test_migration_failure_leaves_font_config_untouched(self):
        result = self.run_install(self.profile)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.config.write_bytes(b"/* mine */")
        real = str(Path(sys.executable).resolve())
        env = self.shim("python3", 'if [[ "$1" == tools/clear-nova-pref.py ]]; then\n'
                        '  [[ "$2" == --check-lock ]] && exit 0\n  exit 64\nfi\n'
                        'exec ' + real + ' "$@"')
        result = self.run_install(self.profile, "--font-size", "12px", env=env)
        self.assert_failed(result)
        self.assertEqual(self.config.read_bytes(), b"/* mine */")
        self.assertFalse(list(self.config.parent.glob("config.css-*.bak")))

    def test_newline_profile_path_cannot_select_another_profile(self):
        alternate = Path(str(self.profile) + "\n")
        alternate.mkdir()
        result = self.run_install("--profile", alternate, "--font-size", "12px")
        self.assert_failed(result)
        self.assertEqual(self.snapshot(), {})
        self.assertEqual(list(alternate.iterdir()), [])

    def test_callback_arguments_are_literal(self):
        self.profile = self.base / 'profile "quotes" \'apostrophe\' ; $(touch HACKED)'
        self.profile.mkdir()
        self.config = self.profile / "chrome/config.css"
        result = self.run_install("--profile", self.profile, "--font-size", "12px")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.config.exists())
        self.assertFalse((self.checkout / "HACKED").exists())

    def test_checkout_config_never_copied_or_backed_up(self):
        (self.checkout / "chrome/config.css").write_bytes(b"checkout config")
        result = self.run_install(self.profile)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.config.exists(), "checkout config belongs to its user, not textfox")
        self.config.write_bytes(b"profile config\r\n")
        before = self.config.stat().st_mtime_ns
        result = self.run_install(self.profile)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.config.read_bytes(), b"profile config\r\n")
        self.assertEqual(self.config.stat().st_mtime_ns, before)
        self.assertFalse(list(self.profile.glob("chrome-*.bak/config.css")))

    def test_failed_source_copy_stops_install(self):
        result = self.run_install(self.profile, env=self.shim("cp", "exit 51"))
        self.assert_failed(result)

    def test_failed_backup_move_stops_install(self):
        self.config.parent.mkdir()
        (self.config.parent / "browser.css").write_text("old browser")
        result = self.run_install(self.profile, env=self.shim("mv", "exit 52"))
        self.assert_failed(result)
        self.assertEqual((self.config.parent / "browser.css").read_text(), "old browser")

    def test_failed_backup_directory_stops_install(self):
        self.config.parent.mkdir()
        (self.config.parent / "browser.css").write_text("old browser")
        result = self.run_install(self.profile, env=self.shim("mkdir", "exit 53"))
        self.assert_failed(result)

    def test_failed_preferences_read_stops_install(self):
        result = self.run_install(self.profile, env=self.shim("awk", "exit 66"))
        self.assert_failed(result)

    def test_failed_preferences_stage_stops_install(self):
        result = self.run_install(self.profile, env=self.shim("mktemp", "exit 67"))
        self.assert_failed(result)

    def test_failed_preferences_replace_stops_install(self):
        real = shutil.which("mv")
        result = self.run_install(self.profile, env=self.shim(
            "mv", 'case "$*" in *user.js*) exit 54;; esac\nexec ' + real + ' "$@"'))
        self.assert_failed(result)

    def test_failed_preferences_backup_keeps_original(self):
        original = b'user_pref("sentinel", 7);\n'
        (self.profile / "user.js").write_bytes(original)
        real = shutil.which("cp")
        result = self.run_install(self.profile, env=self.shim(
            "cp", 'case "$*" in *user.js*) exit 55;; esac\nexec ' + real + ' "$@"'))
        self.assert_failed(result)
        self.assertEqual((self.profile / "user.js").read_bytes(), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
