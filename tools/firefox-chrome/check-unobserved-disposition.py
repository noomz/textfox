#!/usr/bin/env python3
"""Verify every unobserved expanded selector has exactly one disposition."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
LOCATION = re.compile(r'`(chrome/[^`:]+\.css:\d+)`')


def check(evidence, disposition):
    missing = [LOCATION.search(line).group(1) for line in evidence.splitlines()
               if line.startswith('| `chrome/') and line.endswith('| Not observed |')]
    rows = disposition.split('## Record dispositions\n', 1)
    if len(rows) != 2:
        raise ValueError('disposition section missing')
    locations = [item for line in rows[1].splitlines() if line.startswith('| `chrome/')
                 for item in LOCATION.findall(line.split(' | ', 1)[0])]
    if len(locations) != len(set(locations)):
        raise ValueError('duplicate disposition location')
    if set(missing) != set(locations):
        raise ValueError(f'disposition mismatch: missing={sorted(set(missing)-set(locations))}; extra={sorted(set(locations)-set(missing))}')
    return len(locations)


if __name__ == '__main__':
    count = check((ROOT / 'T2-SELECTOR-EVIDENCE.md').read_text(), (ROOT / 'T2-UNOBSERVED-DISPOSITION.md').read_text())
    print(f'{count} unobserved selector records have one disposition')
