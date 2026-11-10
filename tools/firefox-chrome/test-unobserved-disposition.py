#!/usr/bin/env python3
"""Exercise unobserved-selector disposition reconciliation."""
import runpy
from pathlib import Path

check = runpy.run_path(str(Path(__file__).with_name('check-unobserved-disposition.py')))['check']
evidence = '| `chrome/a.css:1` | `.a` | Not observed |\n| `chrome/a.css:2` | `.b` | `probe` |\n'
disposition = '## Record dispositions\n| `chrome/a.css:1` | Bounded evidence. |\n'
assert check(evidence, disposition) == 1
for bad in ('## Record dispositions\n', disposition + '| `chrome/a.css:1` | Duplicate. |\n',
            disposition + '| `chrome/a.css:2` | Extra. |\n'):
    try:
        check(evidence, bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f'invalid disposition accepted: {bad}')
print('Unobserved selector disposition tests passed')
