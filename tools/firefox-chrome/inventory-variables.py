#!/usr/bin/env python3
"""Inventory CSS custom-property declarations and var() references.

Requires tinycss2 1.4.0. Syntax locations do not prove matching or consumption.
"""
import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess

import tinycss2

ROOT = Path(__file__).resolve().parents[2]
SYNTAX_INVENTORY = runpy.run_path(str(ROOT / 'tools/firefox-chrome/inventory-css.py'))['inventory']


def references(tokens):
    for token in tokens:
        if token.type == 'function' and token.lower_name == 'var':
            args = [item for item in token.arguments if item.type not in ('whitespace', 'comment')]
            if args and args[0].type == 'ident' and args[0].value.startswith('--'):
                yield args[0].value
        for attr in ('arguments', 'content'):
            children = getattr(token, attr, None)
            if children is not None:
                yield from references(children)


def inventory(path):
    result = {}

    def add(name, category, entry):
        result.setdefault(name, {'declarations':[], 'references':[]})[category].append(entry)

    def visit(nodes, context):
        for node in nodes:
            if node.type == 'error':
                raise ValueError(f'{path}:{node.source_line}: {node.message}')
            if node.type == 'declaration':
                entry = {'line':node.source_line, 'context':context, 'property':node.name}
                if node.name.startswith('--'):
                    add(node.name, 'declarations', entry)
                for name in references(node.value):
                    add(name, 'references', entry)
            elif node.type in ('at-rule', 'qualified-rule') and node.content is not None:
                prelude = tinycss2.serialize(node.prelude).strip()
                label = f'@{node.at_keyword} {prelude}'.strip() if node.type == 'at-rule' else prelude
                children = tinycss2.parse_blocks_contents(node.content, skip_whitespace=True, skip_comments=True)
                visit(children, [*context, label])

    visit(tinycss2.parse_stylesheet(path.read_text(), skip_whitespace=True, skip_comments=True), [])
    return result


def check_ledger(names, text):
    heading = '## Custom-property syntax reconciliation\n'
    if heading not in text:
        raise ValueError('custom-property ledger section missing')
    section = text.split(heading, 1)[1].split('\n## ', 1)[0]
    documented = set(re.findall(r'`(--[a-zA-Z0-9_-]+)`', section))
    if documented != names:
        raise ValueError(f'custom-property ledger mismatch: missing={sorted(names - documented)}, extra={sorted(documented - names)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--non-textfox', action='store_true', help='omit --tf-* properties')
    parser.add_argument('--check-ledger', action='store_true', help='verify T2 ledger covers all non-textfox names')
    args = parser.parse_args()
    if args.check_ledger and not args.non_textfox:
        parser.error('--check-ledger requires --non-textfox')
    tracked = subprocess.check_output(['git', 'ls-files', '-z', 'chrome'], cwd=ROOT).decode().split('\0')
    combined = {}
    for name in tracked:
        if not name.endswith('.css') or Path(name).name == 'config.css':
            continue
        variables = inventory(ROOT / name)
        syntax_names = set(SYNTAX_INVENTORY(ROOT / name)['customProperties'])
        missing = syntax_names - variables.keys()
        if missing:
            raise ValueError(f'{name}: custom properties outside declaration inventory: {sorted(missing)}')
        for variable, groups in variables.items():
            if args.non_textfox and variable.startswith('--tf-'):
                continue
            target = combined.setdefault(variable, {'declarations':[], 'references':[]})
            for category, entries in groups.items():
                target[category].extend({'file':name, **entry} for entry in entries)
    if args.check_ledger:
        check_ledger(set(combined), (ROOT / 'T2-INVENTORY.md').read_text())
    print(json.dumps(dict(sorted(combined.items())), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
