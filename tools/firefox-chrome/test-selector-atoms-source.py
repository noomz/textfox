#!/usr/bin/env python3
"""Test bounded selector-atom extraction and source matching."""
import runpy
import tempfile
from pathlib import Path

module = runpy.run_path(str(Path(__file__).with_name('selector-atoms-source.py')))
atoms, search = module['atoms'], module['search']
assert atoms(':is(#foo-bar, .some-class) #foo-bar > image') == ['foo-bar', 'some-class']
assert atoms('findbar::before') == []
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / 'test.xhtml').write_text('<box id="foo-bar"/>\n<!-- some-class -->\n<box id="foo-bar-other"/>\n')
    result = search(root, ['test.xhtml'], ['foo-bar', 'some-class', 'missing'])
    assert result['foo-bar'] == [{'file': 'test.xhtml', 'line': 1}]
    assert result['some-class'] == [{'file': 'test.xhtml', 'line': 2}]
    assert not result['missing']
print('Selector atom source tests passed')
