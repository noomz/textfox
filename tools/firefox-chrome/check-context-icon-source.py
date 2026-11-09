#!/usr/bin/env python3
"""Compare simple context-icon ID candidates with one supplied Firefox markup file.

Requires tinycss2 1.4.0. A missing ID may be defined dynamically or elsewhere.
"""
import argparse
import json
from pathlib import Path
import re
import runpy

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = runpy.run_path(str(ROOT / 'tools/firefox-chrome/inventory-css.py'))['inventory']
ID = re.compile(r'#([a-zA-Z_][a-zA-Z0-9_-]*)')
ATTRIBUTE = re.compile(r'''\bid\s*=\s*["']([^"']+)["']''')


def compare(source):
    rules = INVENTORY(ROOT / 'chrome/icons.css')['rules']
    scoped = [r for r in rules if any('#contentAreaContextMenu' in c for c in r['context'])]
    skipped = [r for r in scoped if '\\' in r['selector']]
    candidates = sorted({match for rule in scoped if rule not in skipped
                         for match in ID.findall(rule['selector'])})
    definitions = set(ATTRIBUTE.findall(source.read_text()))
    return {'source':str(source), 'ruleRecords':len(scoped), 'candidateIds':len(candidates),
            'found': [name for name in candidates if name in definitions],
            'notInFile': [name for name in candidates if name not in definitions],
            'skippedEscapedSelectors': skipped}


def compare_live(source_result, live_file, context='link'):
    candidates = set(source_result['found'] + source_result['notInFile'])
    profiles = []
    for line in live_file.read_text().splitlines():
        sample = json.loads(line)
        if context == 'link':
            fixture = sample['item']
        elif context == 'pdf':
            fixture = sample.get('pdfEditor')
        else:
            fixture = sample.get('otherContexts', {}).get(context)
        if not fixture:
            raise ValueError(f'live sample has no {context} context fixture')
        items = fixture['menuItems']
        present = {item['id'] for item in items}
        visible = {item['id'] for item in items if not item['hidden'] and item['display'] != 'none'
                   and item['visibility'] == 'visible' and item['width'] > 0 and item['height'] > 0}
        profiles.append({'context':context, 'nova':sample['firefox']['nova'], 'iconPref':sample['firefox']['iconPref'],
                         'version':sample['firefox']['version'], 'build':sample['firefox']['build'],
                         'candidatePresent':sorted(candidates & present),
                         'candidateVisible':sorted(candidates & visible),
                         'candidateVisibleImages':{item['id']:item['image'] for item in items
                                                   if item['id'] in candidates & visible},
                         'candidateAbsent':sorted(candidates - present)})
    if not profiles:
        raise ValueError('live probe output is empty')
    return profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Firefox browser-context.inc.xhtml to inspect')
    parser.add_argument('--live-jsonl', type=Path, help='output from probe-context-icon.py')
    parser.add_argument('--context', choices=('link', 'page', 'input', 'image', 'selection', 'video',
                                               'video-playing-unmuted', 'video-playing', 'video-before-pause', 'pdf'),
                        default='link', help='live fixture to compare')
    args = parser.parse_args()
    result = compare(args.source)
    if args.live_jsonl:
        result['liveProfiles'] = compare_live(result, args.live_jsonl, args.context)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
