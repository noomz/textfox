#!/usr/bin/env python3
"""Reconcile expanded CSS rule records with bounded live selector snapshots."""
import argparse
import json
from pathlib import Path


def collect(records, observations):
    index = {(r['file'], r['line']): r for r in records}
    if len(index) != len(records):
        raise ValueError('duplicate selector locations in inventory')
    matched = {key: set() for key in index}
    seen = set()

    def visit(value, label, path):
        if isinstance(value, dict):
            if {'file', 'line', 'selector', 'expandedSelector', 'status'} <= value.keys():
                seen.add(label)
                key = value['file'], value['line']
                record = index.get(key)
                if record is None or any(record[field] != value[field] for field in ('selector', 'expandedSelector')):
                    raise ValueError(f'stale or unknown observation {label}:{path}: {key}')
                if value['status'] == 'structural-match':
                    matched[key].add(f'{label}:{path}')
                return
            for name, child in value.items():
                visit(child, label, f'{path}.{name}' if path else name)
        elif isinstance(value, list):
            for child in value:
                visit(child, label, path)

    for label, observation in observations:
        visit(observation, label, '')
    missing = {label for label, _ in observations} - seen
    if missing:
        raise ValueError(f'no selector records in observations: {sorted(missing)}')
    return matched


def render(records, matched):
    covered = sum(bool(v) for v in matched.values())
    lines = ['# Expanded selector evidence', '',
             f'{covered} of {len(records)} syntactic records structurally matched in at least one supplied snapshot. '
             'Other records are not observed in those snapshots; this is not proof they cannot match.',
             'A match may be hidden, come from a synthetic control or prior lazy DOM, or sit under an inactive condition. '
             'Grouped selectors need branch-specific checks; pseudo-elements are not queried. '
             'See probe code and `T2-INVENTORY.md` for conditions and live versus synthetic provenance.', '',
             '| CSS record | Selector | Matching snapshot labels and JSON paths |', '|---|---|---|']
    for record in records:
        key = record['file'], record['line']
        selector = record['selector'].replace('\n', ' ').replace('|', '&#124;')
        references = ', '.join(f'`{label}`' for label in sorted(matched[key])) or 'Not observed'
        lines.append(f"| `{key[0]}:{key[1]}` | `{selector}` | {references} |")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory', type=Path)
    parser.add_argument('observations', nargs='+', help='LABEL=JSONL file; labels identify probe and fixture')
    args = parser.parse_args()
    records = json.loads(args.inventory.read_text())
    inputs = []
    for arg in args.observations:
        label, separator, filename = arg.partition('=')
        if not separator or not label or not filename:
            parser.error(f'expected LABEL=FILE, got {arg!r}')
        path = Path(filename)
        lines = path.read_text().splitlines()
        if not lines:
            parser.error(f'empty observation file: {path}')
        for line in lines:
            inputs.append((label, json.loads(line)))
    print(render(records, collect(records, inputs)), end='')


if __name__ == '__main__':
    main()
