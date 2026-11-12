#!/usr/bin/env python3
"""Prove every textfox.config option reaches the generated CSS or preferences."""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def evaluate(nixpkgs_path, variants):
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
        json.dump(variants, handle)
        variants_file = handle.name
    expression = f'''import {ROOT}/tools/firefox-chrome/check-option-wiring.nix {{
      nixpkgsPath = {nixpkgs_path};
      variants = builtins.fromJSON (builtins.readFile {variants_file});
    }}'''
    try:
        output = subprocess.check_output(
            ['nix', 'eval', '--offline', '--impure', '--json', '--expr', expression],
            text=True, timeout=600)
    finally:
        Path(variants_file).unlink()
    return json.loads(output)


def variant_value(name, default, unsupported):
    if isinstance(default, bool):
        return not default
    if isinstance(default, str):
        return f'sentinel-{name.rsplit(".", 1)[-1].lower()}-value;'
    unsupported.append(f'{name}: default {default!r} has no probe value')
    return None


def main():
    if len(sys.argv) != 2:
        raise SystemExit(f'usage: {Path(sys.argv[0]).name} <nixpkgs store path>')
    nixpkgs_path = sys.argv[1]
    if not re.fullmatch(r'/nix/store/[a-zA-Z0-9._/-]+', nixpkgs_path):
        raise SystemExit(f'{nixpkgs_path} must be a literal /nix/store path')

    scaffold = evaluate(nixpkgs_path, [])
    options = [option for option in scaffold['paths'] if option['visible']]
    if len(options) < 19:
        raise SystemExit(f'expected at least 19 visible options, found {len(options)}')

    unsupported = []
    variants = []
    for option in options:
        name = '.'.join(option['path'])
        value = variant_value(name, option['default'], unsupported)
        if value is not None:
            variants.append({'path': option['path'], 'value': value})

    baseline = scaffold['baseline']
    results = evaluate(nixpkgs_path, variants)['results']
    effects = {}
    failures = list(unsupported)
    for result in results:
        name = '.'.join(result['path'])
        css_changed = result['configCss'] != baseline['configCss']
        prefs_changed = result['configPrefs'] != baseline['configPrefs']
        value = result['value']
        if isinstance(value, str):
            if value not in result['configCss'] and value not in json.dumps(result['configPrefs']):
                failures.append(f'{name}: configured value never reaches an artifact')
        elif not (css_changed or prefs_changed):
            failures.append(f'{name}: flipping the option changes nothing')
        effects[name] = {
            'css': css_changed,
            'prefs': prefs_changed,
            'value': value,
        }

    print(json.dumps({
        'options': len(options),
        'cssOnly': sorted(n for n, e in effects.items() if e['css'] and not e['prefs']),
        'prefsOnly': sorted(n for n, e in effects.items() if e['prefs'] and not e['css']),
        'both': sorted(n for n, e in effects.items() if e['css'] and e['prefs']),
    }, indent=2))
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', file=sys.stderr)
        sys.exit(1)
    print(f'every one of {len(results)} options reaches the generated artifacts')


if __name__ == '__main__':
    main()
