#!/usr/bin/env python3
"""Expand tracked nested selectors into queryable candidates.

Requires tinycss2 1.4.0. This is a bounded DOM inventory aid, not a CSS
cascade engine. Pseudo-elements and conditions still require separate review.
"""
import json
from pathlib import Path
import runpy
import subprocess

import tinycss2

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = runpy.run_path(str(ROOT / 'tools/firefox-chrome/inventory-css.py'))['inventory']


def branches(selector):
    tokens = tinycss2.parse_component_value_list(selector)
    parts = [[]]
    for token in tokens:
        if token.type == 'error':
            raise ValueError(f'invalid selector: {selector}')
        if token.type == 'literal' and token.value == ',':
            parts.append([])
        else:
            parts[-1].append(token)
    result = [tinycss2.serialize(part).strip() for part in parts]
    if any(not part for part in result):
        raise ValueError(f'empty selector branch: {selector}')
    return result


def unsafe_ampersand(tokens):
    for token in tokens:
        if token.type in ('string', 'ident') and '&' in token.value:
            return True
        for attr in ('arguments', 'content'):
            children = getattr(token, attr, None)
            if children is not None and unsafe_ampersand(children):
                return True
    return False


def expand(rule):
    parent = None
    for selector in [*(c for c in rule['context'] if not c.startswith('@')), rule['selector']]:
        if parent is None:
            parent = selector
            continue
        if unsafe_ampersand(tinycss2.parse_component_value_list(selector)):
            raise ValueError(f'unsupported escaped or quoted nesting marker: {selector}')
        expanded = []
        for branch in branches(selector):
            if '&' in branch:
                expanded.append(branch.replace('&', f':is({parent})'))
            else:
                expanded.append(f':is({parent}) {branch}')
        parent = ', '.join(expanded)
    return parent


def main():
    tracked = subprocess.check_output(['git', 'ls-files', '-z', 'chrome'], cwd=ROOT).decode().split('\0')
    result = []
    for file in tracked:
        if not file.endswith('.css') or Path(file).name == 'config.css':
            continue
        for rule in INVENTORY(ROOT / file)['rules']:
            result.append({'file':file, **rule, 'expandedSelector':expand(rule),
                           'conditions':[c for c in rule['context'] if c.startswith('@')]})
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
