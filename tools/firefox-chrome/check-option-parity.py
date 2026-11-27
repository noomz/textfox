#!/usr/bin/env python3
"""Compare a manual config.css with the Nix-generated CSS for non-default options."""
import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]

MANUAL_CONFIG_CSS = ''':root {
  --tf-font-size: 17px;
  --tf-rounding: 4px;
  --tf-urlbar-radius: 0px;
  --tf-text-transform: uppercase;
  --tf-display-horizontal-tabs: block;
  --tf-display-titles: none;
  --tf-container-frame: 1;
  --tf-theme-gradient: 1;
  --tf-theme-images: 1;
  --tf-theme-scrim: 60%;
  --tf-theme-colors: 0;
  --tf-popup-bg: rgb(10, 20, 30);
  --tf-newtab-logo: "hi";
}
'''

NIX_OPTIONS = '''{
  textfox.config.font.size = "17px";
  textfox.config.border.radius = "4px";
  textfox.config.urlbar.radius = "0px";
  textfox.config.textTransform = "uppercase";
  textfox.config.tabs.horizontal.enable = true;
  textfox.config.displayTitles = false;
  textfox.config.containerFrame = true;
  textfox.config.themeGradient = true;
  textfox.config.themeImages = true;
  textfox.config.themeScrim = "60%";
  textfox.config.themeColors = false;
  textfox.config.colors.popup.background = "rgb(10, 20, 30)";
  textfox.config.newtabLogo = "hi";
  system.stateVersion = "26.05";
}'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('nixpkgs_path')
    args = parser.parse_args()
    if not re.fullmatch(r'/nix/store/[a-zA-Z0-9._/-]+', args.nixpkgs_path):
        parser.error('nixpkgs_path must be a literal /nix/store path')
    expression = f'''let src={args.nixpkgs_path}; ev=import (src + "/nixos/lib/eval-config.nix") {{
      system=builtins.currentSystem;
      modules=[ ./nix/modules/options.nix {NIX_OPTIONS} ];
    }}; in ev.config.textfox.configCss'''
    css = json.loads(subprocess.check_output(
        ['nix', 'eval', '--offline', '--impure', '--json', '--expr', expression],
        cwd=ROOT, text=True, timeout=120))
    inspect = runpy.run_path(str(ROOT / 'tools/firefox-chrome/check-config-defaults.py'))['inspect']
    manual_meta, manual = inspect(MANUAL_CONFIG_CSS)
    nix_meta, generated = inspect(css)

    configured = sorted(set(re.findall(r'(--tf-[\w-]+)\s*:', MANUAL_CONFIG_CSS)))
    differences = {key: {'manual': manual[key], 'nix': generated[key]}
                   for key in manual if manual[key] != generated[key]}
    print(json.dumps({
        'manualFirefox': manual_meta,
        'nixFirefox': nix_meta,
        'configured': {key: manual[key] for key in configured},
        'generated': {key: generated[key] for key in configured},
        'differences': differences,
    }, indent=2))

    failures = []
    if differences:
        failures.append(f'manual and generated styles disagree: {differences}')
    for key in configured:
        if not manual[key]:
            failures.append(f'{key} is empty in the manual profile')
    if manual['--tf-display-horizontal-tabs'] != 'block':
        failures.append('horizontal tabs do not show as block')
    if manual['--tf-display-titles'] != 'none':
        failures.append('titles are not hidden')
    if manual['--tf-container-frame'] != '1':
        failures.append('container frames are not on')
    if manual['--tf-theme-gradient'] != '1' or manual['--tf-theme-images'] != '1':
        failures.append('theme gradient and images are not on')
    if manual['--tf-theme-scrim'] != '60%':
        failures.append('theme scrim does not follow configuration')
    if manual['--tf-theme-colors'] != '0' or manual['--tf-popup-bg'] != 'rgb(10, 20, 30)':
        failures.append('theme colors switch or popup background does not follow configuration')
    if manual['--tf-font-size'] != '17px' or manual['--tf-rounding'] != '4px':
        failures.append('sizes do not follow configuration')
    if manual['--tf-urlbar-radius'] != '0px':
        failures.append('URL bar radius does not follow configuration')
    if manual['--tf-newtab-logo-rendered'] != '"hi"':
        failures.append(f'new-tab logo renders {manual["--tf-newtab-logo-rendered"]!r}')
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', file=sys.stderr)
        sys.exit(1)
    print(f'manual and generated CSS agree on {len(configured)} configured options')


if __name__ == '__main__':
    main()
