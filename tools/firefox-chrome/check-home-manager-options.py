#!/usr/bin/env python3
"""Check what the home-manager module delivers for configured options."""
import argparse
from pathlib import Path
import json
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
STORE = Path('/nix/store')
if not STORE.is_dir():
    STORE = Path.home() / '.nix-portable/nix/store'


def host_path(path, depth=0):
    """Map a /nix/store path onto this host's store and follow symlinks inside it."""
    path = Path(path)
    if depth > 20:
        raise RuntimeError(f'symlink loop at {path}')
    if path.is_absolute() and path.parts[:3] == ('/', 'nix', 'store'):
        path = STORE.joinpath(*path.parts[3:])
    try:
        relative = path.relative_to(STORE)
    except ValueError:
        return path
    current = STORE
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            target = Path(current.readlink())
            current = host_path(target if target.is_absolute() else current.parent / target, depth + 1)
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('nixpkgs_path')
    parser.add_argument('home_manager_path')
    args = parser.parse_args()
    for path in (args.nixpkgs_path, args.home_manager_path):
        if not re.fullmatch(r'/nix/store/[a-zA-Z0-9._/-]+', path):
            parser.error(f'{path} must be a literal /nix/store path')
    expression = f'''(import {ROOT}/tools/firefox-chrome/check-home-manager.nix {{
      nixpkgsPath = {args.nixpkgs_path};
      homeManagerPath = {args.home_manager_path};
    }}).activationPackage'''
    generation = Path(subprocess.check_output(
        ['nix', 'build', '--no-link', '--print-out-paths', '--impure', '--expr', expression],
        cwd=ROOT, text=True, timeout=1800).strip())
    profile = generation / 'home-files/.config/mozilla/firefox/test'
    user_js = host_path(profile / 'user.js').read_text()
    config_css = host_path(profile / 'chrome/config.css').read_text()
    shipped = sorted(p.name for p in host_path(profile / 'chrome').iterdir() if p.name.endswith('.css'))

    expected_prefs = {
        'shyfox.enable.ext.mono.toolbar.icons': True,
        'shyfox.enable.ext.mono.context.icons': True,
        'shyfox.enable.context.menu.icons': True,
        'browser.nova.enabled': False,
    }
    applied = {name: re.search(rf'user_pref\("{re.escape(name)}", (true|false)\);', user_js)
               for name in expected_prefs}
    declared = re.findall(r'(--tf-[\w-]+)\s*:', (ROOT / 'chrome/defaults.css').read_text())
    delivered = set(re.findall(r'(--tf-[\w-]+)\s*:', config_css))
    report = {
        'generation': str(generation),
        'profile': str(profile),
        'prefs': {name: match.group(1) if match else None for name, match in applied.items()},
        'missingVariables': sorted(set(declared) - delivered),
        'extraVariables': sorted(delivered - set(declared)),
        'shippedStylesheets': shipped,
        'configuredBorder': re.search(r'--tf-border-width: ([^;]+);', config_css).group(1).strip(),
        'configuredVerticalTabs': re.search(r'--tf-display-vertical-tabs: ([^;]+);', config_css).group(1).strip(),
    }
    print(json.dumps(report, indent=2))

    failures = []
    for name, value in expected_prefs.items():
        observed = report['prefs'][name]
        if observed != str(value).lower():
            failures.append(f'preference {name} is {observed!r}, expected {str(value).lower()!r}')
    if report['missingVariables']:
        failures.append(f'generated config.css is missing {report["missingVariables"]}')
    if report['extraVariables']:
        failures.append(f'generated config.css invents {report["extraVariables"]}')
    if report['configuredBorder'] != '7px':
        failures.append(f'configured border is {report["configuredBorder"]!r}')
    if report['configuredVerticalTabs'] != 'none':
        failures.append(f'configured vertical tabs are {report["configuredVerticalTabs"]!r}')
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', file=sys.stderr)
        sys.exit(1)
    print('home-manager delivers every configured option')


if __name__ == '__main__':
    main()
