#!/usr/bin/env python3
"""Installer regressions using only disposable checkouts and profiles."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


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

    def run_install(self, *args, env=None, input=""):
        return subprocess.run(["/bin/bash", "tf-install.sh", *map(str, args)],
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
