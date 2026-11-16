#!/usr/bin/env python3
"""Tests for bounded Firefox source custom-property searching."""
import runpy
import tempfile
from pathlib import Path

module = runpy.run_path(str(Path(__file__).with_name('inventory-firefox-variable-source.py')))
search = module['search']
markdown = module['markdown']

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / 'sample.css').write_text('--color: red; color: var(--color); --color-extra: blue;\n/* --color */\n')
    (root / 'binary').write_bytes(b'--color\0binary')
    result = search(root, ['sample.css', 'binary'], ['--color', '--missing'])
    assert result['--color'] == [{'file': 'sample.css', 'line': 1}, {'file': 'sample.css', 'line': 2}]
    assert result['--missing'] == []
    report = markdown({'revision': 'abc', 'scopes': ['sample'], 'filesSearched': 2, 'matches': result})
    assert '[sample.css:1](https://github.com/mozilla-firefox/firefox/blob/abc/sample.css#L1)' in report
    assert '| `--missing` | 0 | None in searched scopes |' in report
print('Firefox variable source search tests passed')
