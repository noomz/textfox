#!/usr/bin/env python3
"""Search ID/class atoms of unobserved selector records in tagged Firefox source.

Textual atom matches include comments, tests, and unrelated contexts. This is a
heuristic, not selector parsing or proof that Firefox contains a full selector.
"""
import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess

ROOT = Path(__file__).resolve().parents[2]
VARIABLE_SOURCE = runpy.run_path(str(ROOT / 'tools/firefox-chrome/inventory-firefox-variable-source.py'))
search = VARIABLE_SOURCE['search']
REVISION = VARIABLE_SOURCE['REVISION']
SCOPES = VARIABLE_SOURCE['SCOPES']
ATOM = re.compile(r'(?<![\w-])[.#]([a-zA-Z_][\w-]+)')
LOCATION = re.compile(r'^\| `(chrome/[^`]+):(\d+)` \|.*\| Not observed \|$')


def atoms(selector):
    return list(dict.fromkeys(ATOM.findall(selector)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('inventory', type=Path)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != REVISION:
        parser.error(f'expected {REVISION}, got {revision}')
    subprocess.run(['git', '-C', str(source), 'diff', '--quiet', 'HEAD', '--', *SCOPES], check=True)
    tracked = subprocess.check_output(['git', '-C', str(source), 'ls-files', '-z', '--', *SCOPES]).decode().split('\0')
    missing = [path for path in tracked if path and not (source / path).is_file()]
    if missing:
        parser.error(f'scope has {len(missing)} missing files, e.g. {missing[0]}')
    paths = sorted(path for path in tracked if path)
    records = json.loads(args.inventory.read_text())
    index = {(r['file'], r['line']): r for r in records}
    if len(index) != len(records):
        parser.error('duplicate selector locations')
    selected = []
    for line in args.evidence.read_text().splitlines():
        match = LOCATION.match(line)
        if match:
            key = match.group(1), int(match.group(2))
            if key not in index:
                parser.error(f'evidence has unknown location: {key}')
            rendered = index[key]['selector'].replace('\n', ' ').replace('|', '&#124;')
            parts = line.split('`')
            if len(parts) < 4 or parts[3] != rendered:
                parser.error(f'evidence has stale selector: {key}')
            selected.append(index[key])
    if not selected:
        parser.error('no unobserved records in evidence')
    names = sorted({name for record in selected for name in atoms(record['expandedSelector'])})
    hits = search(source, paths, names)
    base = f'https://github.com/mozilla-firefox/firefox/blob/{revision}/'
    lines = ['# Unobserved selector atom source search', '',
             f'{len(selected)} selector records marked not observed in `T2-SELECTOR-EVIDENCE.md`; '
             f'{len(paths)} tracked files searched under `browser/` and `toolkit/` at `{revision}`.',
             'ID/class atoms are extracted heuristically from expanded selectors; tag names, attributes, '
             'pseudo-elements, nesting relationships, and conditions are not checked. '
             'Hits include unrelated contexts, comments, and tests; no hit does not prove removal. '
             'Generated extension IDs need not appear literally in Firefox source.', '',
             '| CSS record | Heuristic atoms and example tagged source lines |', '|---|---|']
    for record in selected:
        entries = []
        for name in atoms(record['expandedSelector']):
            found = hits[name]
            if found:
                example = found[0]
                entries.append(f"`{name}`: [{example['file']}:{example['line']}]({base}{example['file']}#L{example['line']}) ({len(found)} lines)")
            else:
                entries.append(f'`{name}`: no textual match in searched scopes')
        text = '; '.join(entries) if entries else 'No ID/class atom extracted'
        lines.append(f"| `{record['file']}:{record['line']}` | {text} |")
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
