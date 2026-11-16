#!/usr/bin/env python3
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("config_fonts", ROOT / "tools/config-fonts.py")
fonts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fonts)


class ConfigFontsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tf-font-config-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self.base / "chrome/config.css"
        self.config.parent.mkdir()
        self.original = b"\xef\xbb\xbf/* caf\xc3\xa9 */\r\n:root { --tf-font-size: 16px; }\r\n\r\n"
        self.config.write_bytes(self.original)
        self.config.chmod(0o640)
        self.called = self.base / "callback called"
        self.command = [sys.executable, "-c",
                        "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('called')",
                        str(self.called)]

    def install(self, values=None, command=None):
        fonts.install(self.config, values or {"size": "13px"}, command or self.command)

    def backups(self):
        return sorted(self.config.parent.glob("config.css-*.bak"))

    def assert_original(self):
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.assertEqual(list(self.config.parent.glob(".config.css-textfox-*")), [])

    def assert_retry(self):
        self.install()
        self.assertTrue(self.config.read_bytes().startswith(b"\xef\xbb\xbf/* >>> textfox managed fonts >>> */\n"))
        self.assertTrue(self.config.read_bytes().endswith(self.original[3:]))
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.assertTrue(all(p.read_bytes() == self.original for p in self.backups()))

    def test_callback_failure_keeps_config_and_creates_no_backups(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.install(command=[sys.executable, "-c", "import sys; sys.exit(44)"])
        self.assert_original()
        self.assertEqual(self.backups(), [])
        self.assert_retry()

    def test_callback_change_keeps_newer_config(self):
        command = [sys.executable, "-c",
                   "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'newer bytes')",
                   str(self.config)]
        with self.assertRaisesRegex(ValueError, "changed during installation"):
            self.install(command=command)
        self.assertEqual(self.config.read_bytes(), b"newer bytes")
        self.assertEqual(self.backups(), [])

    def test_noop_still_detects_callback_change(self):
        self.install()
        command = [sys.executable, "-c",
                   "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'newer bytes')",
                   str(self.config)]
        with self.assertRaisesRegex(ValueError, "changed during installation"):
            self.install(command=command)
        self.assertEqual(self.config.read_bytes(), b"newer bytes")
        self.assertEqual(len(self.backups()), 1)

    def test_same_bytes_replacement_during_callback_is_detected(self):
        command = [sys.executable, "-c",
                   "from pathlib import Path; import sys, os; p=Path(sys.argv[1]); "
                   "q=p.with_suffix('.tmp'); q.write_bytes(p.read_bytes()); os.replace(q, p)",
                   str(self.config)]
        with self.assertRaisesRegex(ValueError, "changed during installation"):
            self.install(command=command)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(self.backups(), [])

    def test_chrome_directory_replacement_is_detected(self):
        command = [sys.executable, "-c",
                   "from pathlib import Path; import sys; p=Path(sys.argv[1]); "
                   "p.rename(p.with_name('saved')); p.mkdir()", str(self.config.parent)]
        with self.assertRaisesRegex(ValueError, "chrome directory changed"):
            self.install(command=command)
        self.assertEqual((self.base / "saved/config.css").read_bytes(), self.original)
        self.assertFalse(self.config.exists())

    def test_backup_creation_failure_is_retryable(self):
        with patch.object(fonts.tempfile, "mkstemp", side_effect=OSError("backup unavailable")):
            with self.assertRaisesRegex(OSError, "backup unavailable"):
                self.install()
        self.assertTrue(self.called.exists())
        self.assert_original()
        self.assertEqual(self.backups(), [])
        self.assert_retry()

    def test_incomplete_backup_is_cleaned_on_flush_failure(self):
        with patch.object(fonts.os, "fsync", side_effect=OSError("backup flush failed")):
            with self.assertRaisesRegex(OSError, "backup flush failed"):
                self.install()
        self.assert_original()
        self.assertEqual(self.backups(), [])
        self.assert_retry()

    def test_stage_creation_failure_keeps_completed_backup(self):
        real = fonts.tempfile.mkstemp

        def fail_stage(**kwargs):
            if kwargs["prefix"].startswith("."):
                raise OSError("stage unavailable")
            return real(**kwargs)

        with patch.object(fonts.tempfile, "mkstemp", side_effect=fail_stage):
            with self.assertRaisesRegex(OSError, "stage unavailable"):
                self.install()
        self.assert_original()
        self.assertEqual([p.read_bytes() for p in self.backups()], [self.original])
        self.assert_retry()
        self.assertEqual(len(self.backups()), 2)

    def test_stage_flush_failure_keeps_backup_and_cleans_stage(self):
        real = fonts.os.fsync
        calls = []

        def fail_stage(fd):
            calls.append(fd)
            if len(calls) == 2:
                raise OSError("stage flush failed")
            return real(fd)

        with patch.object(fonts.os, "fsync", side_effect=fail_stage):
            with self.assertRaisesRegex(OSError, "stage flush failed"):
                self.install()
        self.assert_original()
        self.assertEqual([p.read_bytes() for p in self.backups()], [self.original])
        self.assert_retry()

    def test_replace_failure_is_retryable_and_retains_backups(self):
        with patch.object(fonts.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                self.install()
        self.assert_original()
        self.assertEqual([p.read_bytes() for p in self.backups()], [self.original])
        self.assert_retry()
        self.assertEqual(len(self.backups()), 2)

    def test_change_while_staging_keeps_external_edit(self):
        real = fonts.os.fsync
        calls = []

        def change_stage(fd):
            calls.append(fd)
            if len(calls) == 2:
                self.config.write_bytes(b"external edit")
            return real(fd)

        with patch.object(fonts.os, "fsync", side_effect=change_stage):
            with self.assertRaisesRegex(ValueError, "changed during installation"):
                self.install()
        self.assertEqual(self.config.read_bytes(), b"external edit")
        self.assertEqual([p.read_bytes() for p in self.backups()], [self.original])
        self.assertEqual(list(self.config.parent.glob(".config.css-textfox-*")), [])

    def test_noop_keeps_inode_mtime_mode_and_backup_count(self):
        self.install()
        before = self.config.stat()
        backups = self.backups()
        self.install()
        after = self.config.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns, before.st_mode),
                         (after.st_ino, after.st_mtime_ns, after.st_mode))
        self.assertEqual(self.backups(), backups)

    def test_omitted_values_are_retained_in_both_directions(self):
        self.install({"family": '"Fira Code", monospace'})
        self.install({"size": "15px"})
        self.install({"family": "serif"})
        self.assertIn(b"--tf-font-family: serif;", self.config.read_bytes())
        self.assertIn(b"--tf-font-size: 15px;", self.config.read_bytes())

    def test_unsafe_input_never_calls_installer(self):
        for data in [b"\0", b"\xfe\xff", b'@import "x";', b'@layer foo;', b'@\\6c ayer foo;',
                     b'@nAmEsPaCe "x";', b'/*', b'"unterminated', b"'bad\nstring'", b'@CHARSET "utf-8";',
                     r':root { --x: \"; } @layer foo {} /* " */'.encode()]:
            with self.subTest(data=data):
                self.config.write_bytes(data)
                with self.assertRaises(ValueError):
                    self.install()
                self.assertFalse(self.called.exists())
                self.assertEqual(self.config.read_bytes(), data)
                self.assertEqual(self.backups(), [])

    def test_escaped_quote_outside_string_is_not_a_string_opener(self):
        original = r':root { --literal-quote: \"; }'.encode()
        self.config.write_bytes(original)
        self.install()
        self.assertTrue(self.config.read_bytes().endswith(original))

    def test_valid_values(self):
        families = ['"JetBrains Mono", monospace', "Fira Code, -apple-system, serif",
                    "'Noto Sans ไทย', sans-serif", '"Name, with comma", monospace']
        for value in families:
            with self.subTest(value=value):
                self.assertEqual(fonts.font_value("family", value), value)
        for value in ["1px", ".5rem", "12.5pt", "100%", "0.1em", "1ch", "2ex"]:
            with self.subTest(value=value):
                self.assertEqual(fonts.font_value("size", value), value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
