#!/usr/bin/env python3
"""List CSS rule preludes and custom property references for T2 inventory.

Requires tinycss2 1.4.0. Output describes syntax, not whether rules match Firefox.
"""
import json
from pathlib import Path
import subprocess

import tinycss2

ROOT = Path(__file__).resolve().parents[2]
def inventory(path):
    text = path.read_text()
    records = []

    def visit(nodes, parents):
        for node in nodes:
            if node.type not in ('qualified-rule', 'at-rule'):
                continue
            prelude = tinycss2.serialize(node.prelude).strip()
            label = f'@{node.at_keyword} {prelude}'.strip() if node.type == 'at-rule' else prelude
            if node.type == 'qualified-rule':
                records.append({'line': node.source_line, 'context': parents, 'selector': label})
            if node.content is not None:
                children = tinycss2.parse_blocks_contents(node.content, skip_whitespace=True, skip_comments=True)
                visit(children, [*parents, label])

    nodes = tinycss2.parse_stylesheet(text, skip_whitespace=True, skip_comments=True)
    visit(nodes, [])

    def variables(tokens):
        for token in tokens:
            if token.type == 'ident' and token.value.startswith('--'):
                yield token.value
            for attr in ('prelude', 'content', 'arguments'):
                children = getattr(token, attr, None)
                if children is not None:
                    yield from variables(children)

    return {'file': str(path.relative_to(ROOT)), 'rules': records,
            'customProperties': sorted(set(variables(nodes)))}


def main():
    tracked = subprocess.check_output(['git', 'ls-files', '-z', 'chrome'], cwd=ROOT).decode().split('\0')
    files = [ROOT / path for path in tracked if path.endswith('.css') and Path(path).name != 'config.css']
    print(json.dumps([inventory(path) for path in files], indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
