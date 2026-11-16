#!/usr/bin/env python3
"""Test per-record selector evidence reconciliation."""
import runpy
from pathlib import Path

module = runpy.run_path(str(Path(__file__).with_name('selector-evidence.py')))
collect = module['collect']
render = module['render']
records = [{'file': 'chrome/a.css', 'line': 1, 'selector': '.a', 'expandedSelector': '.a'},
           {'file': 'chrome/a.css', 'line': 2, 'selector': '.b::before', 'expandedSelector': '.b::before'}]
matched = {'file': 'chrome/a.css', 'line': 1, 'selector': '.a', 'expandedSelector': '.a', 'status': 'structural-match'}
not_found = {**matched, 'status': 'not-found-in-sampled-document'}
result = collect(records, [('probe', {'states': {'control': [matched], 'default': [not_found]}})])
assert result[('chrome/a.css', 1)] == {'probe:states.control'}
assert result[('chrome/a.css', 2)] == set()
text = render(records, result)
assert '`probe:states.control`' in text
assert 'Not observed' in text
try:
    collect(records, [('probe', {**matched, 'selector': '.changed'})])
except ValueError:
    pass
else:
    raise AssertionError('stale observation accepted')
try:
    collect(records, [('empty', {'states': []})])
except ValueError:
    pass
else:
    raise AssertionError('observation without selector records accepted')
print('Selector evidence tests passed')
