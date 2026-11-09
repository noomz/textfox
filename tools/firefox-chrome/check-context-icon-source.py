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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Firefox browser-context.inc.xhtml to inspect')
    args = parser.parse_args()
    print(json.dumps(compare(args.source), indent=2))


if __name__ == '__main__':
    main()
