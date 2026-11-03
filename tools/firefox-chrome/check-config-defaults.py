#!/usr/bin/env python3
"""Compare CSS defaults with Nix-generated defaults in Firefox."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import sys
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def inspect(config_css):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-config-defaults-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        if config_css is not None:
            (profile / 'chrome' / 'config.css').write_text(config_css)
        (profile / 'user.js').write_text(f'user_pref("marionette.port", {port});\n'
            'user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);\n'
            'user_pref("browser.shell.checkDefaultBrowser", false);\n'
            'user_pref("browser.newtabpage.activity-stream.testing.shouldInitializeFeeds", true);\n'
            'user_pref("browser.newtabpage.activity-stream.widgets.enabled", true);\n')
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID}", sandbox='system')
            if process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve():
                raise RuntimeError('unexpected Firefox process or profile')
            names = re.findall(r'(--tf-[\w-]+)\s*:', (ROOT / 'chrome/defaults.css').read_text())
            values = m.execute_script("const s=getComputedStyle(document.documentElement); return Object.fromEntries(arguments[0].map(n=>[n,s.getPropertyValue(n).trim()]));", script_args=[names], sandbox='system')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while not m.execute_script('return location.href === "about:newtab" && !!document.querySelector(".wordmark")'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('new-tab wordmark did not initialize')
                time.sleep(.25)
            values['--tf-newtab-logo-rendered'] = m.execute_script("return getComputedStyle(document.querySelector('.wordmark'), '::before').content")
            return meta, values
        finally:
            try:
                if m:
                    m.delete_session()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('nixpkgs_path')
    args = parser.parse_args()
    if not re.fullmatch(r'/nix/store/[a-zA-Z0-9._/-]+', args.nixpkgs_path):
        parser.error('nixpkgs_path must be a literal /nix/store path')
    expression = f'''let src={args.nixpkgs_path}; ev=import (src + "/nixos/lib/eval-config.nix") {{ system=builtins.currentSystem; modules=[ ./nix/modules/options.nix {{ system.stateVersion="26.05"; }} ]; }}; in ev.config.textfox.configCss'''
    css = json.loads(subprocess.check_output(['nix', 'eval', '--offline', '--impure', '--json', '--expr', expression], cwd=ROOT, text=True, timeout=120))
    manual_meta, manual = inspect(None)
    nix_meta, generated = inspect(css)
    differences = {key:{'manual':manual[key], 'nix':generated[key]} for key in manual if manual[key] != generated[key]}
    print(json.dumps({'manualFirefox':manual_meta, 'nixFirefox':nix_meta, 'differences':differences}, indent=2))
    if differences:
        sys.exit(1)


if __name__ == '__main__':
    main()
