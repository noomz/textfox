#!/usr/bin/env python3
"""Join simple Places icon IDs with bookmark-context live samples.

Requires tinycss2 1.4.0. ID matches do not prove complete selectors or painted icons.
"""
import argparse
import json
from pathlib import Path
import re
import runpy

import tinycss2

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = runpy.run_path(str(ROOT / 'tools/firefox-chrome/inventory-css.py'))['inventory']


def candidate_ids(rules):
    ids = set()
    for rule in rules:
        if not rule['context'] or rule['context'][-1] != '#placesContext':
            continue
        tokens = tinycss2.parse_component_value_list(rule['selector'])
        if any(token.type == 'function' for token in tokens):
            raise ValueError(f"unsupported Places selector: {rule['selector']}")
        ids.update(token.value for token in tokens if token.type == 'hash')
    return ids


def compare_live(ids, samples):
    profiles = []
    for sample in samples:
        if 'placesContext' not in sample:
            continue
        kind = sample.get('placesKind')
        if kind not in ('bookmark', 'folder'):
            raise ValueError(f'unknown Places fixture kind: {kind}')
        items = sample['placesContext']['items']
        present = {item['id'] for item in items}
        visible = {item['id'] for item in items if not item['hidden'] and item['display'] != 'none'
                   and item['visibility'] == 'visible' and item['width'] > 0 and item['height'] > 0}
        meta = sample['firefox']
        profiles.append({'kind':kind, 'nova':meta['nova'], 'iconPref':meta['iconPref'], 'version':meta['version'],
                         'build':meta['build'], 'candidatePresent':sorted(ids & present),
                         'candidateVisible':sorted(ids & visible), 'candidateAbsent':sorted(ids - present),
                         'candidateVisibleImages':{item['id']:item['image'] for item in items
                                                   if item['id'] in ids & visible}})
    if not profiles:
        raise ValueError('live probe output has no Places context samples')
    return profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('live_jsonl', type=Path, help='output from test-bookmarks-toolbar-live.py')
    parser.add_argument('--source', type=Path, help='tagged placesContextMenu.inc.xhtml')
    args = parser.parse_args()
    rules = INVENTORY(ROOT / 'chrome/icons.css')['rules']
    ids = candidate_ids(rules)
    samples = [json.loads(line) for line in args.live_jsonl.read_text().splitlines()]
    result = {'candidateIds':sorted(ids), 'liveProfiles':compare_live(ids, samples)}
    if args.source:
        definitions = set(re.findall(r'''\bid\s*=\s*["']([^"']+)["']''', args.source.read_text()))
        result['source'] = {'file':str(args.source), 'found':sorted(ids & definitions),
                            'notInFile':sorted(ids - definitions)}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
