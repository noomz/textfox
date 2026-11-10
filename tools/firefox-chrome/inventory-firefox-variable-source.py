#!/usr/bin/env python3
"""Find textfox's non-tf custom-property names in a bounded tagged Firefox checkout.

Text matches include comments and do not prove that Firefox consumes a property.
Requires tinycss2 1.4.0 for the textfox inventory.
"""
import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
REVISION = '9a2dc53c369c7ae5172018b35988e1d2ffeff402'
SCOPES = ('browser', 'toolkit')


def search(root, paths, names):
    result = {name: [] for name in names}
    pattern = re.compile(r'(?<![\w-])(?:' + '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True)) + r')(?![\w-])')
    for path in paths:
        data = (root / path).read_bytes()
        if b'\0' in data:
            continue
        for line_number, line in enumerate(data.decode('utf-8', errors='replace').splitlines(), 1):
            for name in set(pattern.findall(line)):
                result[name].append({'file': path, 'line': line_number})
    return result


def markdown(report):
    base = f"https://github.com/mozilla-firefox/firefox/blob/{report['revision']}/"
    lines = ['# Non-textfox-prefixed variable source search', '',
             'Bounded textual search of textfox property names in tracked files at `FIREFOX_159_0_RELEASE`. '
             'Names include both Firefox-owned and textfox-local properties.',
             f"Revision: `{report['revision']}`. Files searched: {report['filesSearched']}.",
             'Scopes: ' + ', '.join(f'`{scope}/`' for scope in report['scopes']) + '.', '',
             'Counts are matching source lines, including comments and tests, not consumers. '
             'Examples are not exhaustive. No match means only no textual match within these scopes; '
             'it does not prove removal or absence from other Firefox files or runtime state.', '',
             '| Property | Matching lines | Example source lines |', '|---|---:|---|']
    for name, hits in report['matches'].items():
        ordered = sorted(hits, key=lambda h: (not h['file'].endswith(('.css', '.scss')), h['file'], h['line']))
        examples = ', '.join(f"[{h['file']}:{h['line']}]({base}{h['file']}#L{h['line']})" for h in ordered[:2]) or 'None in searched scopes'
        lines.append(f'| `{name}` | {len(hits)} | {examples} |')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Firefox git checkout at FIREFOX_159_0_RELEASE')
    parser.add_argument('--markdown', action='store_true', help='render bounded summary with source links')
    args = parser.parse_args()
    source = args.source.resolve()
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != REVISION:
        parser.error(f'expected {REVISION}, got {revision}')
    for scope in SCOPES:
        if not (source / scope).is_dir():
            parser.error(f'sparse checkout missing scope {scope}')
    subprocess.run(['git', '-C', str(source), 'diff', '--quiet', 'HEAD', '--', *SCOPES], check=True)
    tracked = subprocess.check_output(['git', '-C', str(source), 'ls-files', '-z', '--', *SCOPES]).decode().split('\0')
    missing = [path for path in tracked if path and not (source / path).is_file()]
    if missing:
        parser.error(f'scope has {len(missing)} missing tracked files; complete sparse checkout first (e.g. {missing[0]})')
    paths = sorted(path for path in tracked if path)
    if not paths:
        parser.error('no tracked source files found')
    variables = json.loads(subprocess.check_output([
        sys.executable, str(ROOT / 'tools/firefox-chrome/inventory-variables.py'), '--non-textfox'], cwd=ROOT))
    matches = search(source, paths, variables)
    report = {'revision': revision, 'scopes': SCOPES, 'filesSearched': len(paths), 'matches': matches}
    print(markdown(report), end='') if args.markdown else print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
