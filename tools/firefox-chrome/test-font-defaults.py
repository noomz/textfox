#!/usr/bin/env python3

import csv
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = ("SF Mono", "Menlo", "Consolas", "monospace")


def css_font_family(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    root = re.search(r"(?m)^:root\s*\{([^{}]*)\}", css)
    if root is None:
        raise ValueError("missing :root CSS rule")
    declarations = re.findall(r"(?m)^\s*--tf-font-family:\s*([^;]+);", root.group(1))
    if len(declarations) != 1:
        raise ValueError("expected one --tf-font-family declaration in :root")
    return tuple(part.strip() for part in next(csv.reader(declarations, skipinitialspace=True)))


class FontDefaultsTest(unittest.TestCase):
    def test_default_order_and_parity(self):
        nix = (ROOT / "nix/modules/options.nix").read_text()
        option = re.search(
            r'(?m)^      font = \{\s*family = mkOption \{\s*type = str;\s*default = ("(?:\\.|[^"\\])*");',
            nix,
        )
        self.assertIsNotNone(option, "missing Nix textfox.config.font.family default")
        readme = (ROOT / "readme.md").read_text()
        example = re.search(r"(?ms)^#### Defaults\s*```css\s*\n(.*?)^```", readme)
        self.assertIsNotNone(example, "missing readme Defaults CSS example")
        defaults = {
            "chrome/defaults.css": css_font_family((ROOT / "chrome/defaults.css").read_text()),
            "nix/modules/options.nix": tuple(
                part.strip() for part in next(csv.reader([json.loads(option.group(1))], skipinitialspace=True))
            ),
            "readme.md Defaults": css_font_family(example.group(1)),
        }
        for location, family in defaults.items():
            with self.subTest(location=location):
                self.assertEqual(family, EXPECTED)

    def test_nix_generated_css_uses_option(self):
        nix = (ROOT / "nix/modules/options.nix").read_text()
        self.assertRegex(nix, r"(?m)^\s*--tf-font-family:\s*\$\{cfg\.font\.family\};")


if __name__ == "__main__":
    unittest.main()
