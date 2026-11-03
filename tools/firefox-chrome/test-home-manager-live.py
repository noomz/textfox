#!/usr/bin/env python3
"""Evaluate Home Manager Firefox files and inspect them in a disposable profile.

Materializes evaluated home.file entries without executing Home Manager activation.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('nixpkgs_path')
    parser.add_argument('home_manager_path')
    args = parser.parse_args()
    if not all(re.fullmatch(r'/nix/store/[a-zA-Z0-9._/-]+', p) for p in (args.nixpkgs_path, args.home_manager_path)):
        parser.error('paths must be literal /nix/store paths')
    expression = f'''let x=import ./tools/firefox-chrome/check-home-manager.nix {{ nixpkgsPath={args.nixpkgs_path}; homeManagerPath={args.home_manager_path}; }}; f=x.config.home.file; in {{ chrome=f.".config/mozilla/firefox/test/chrome".source; config=f.".config/mozilla/firefox/test/chrome/config.css".text; entry=f.".config/mozilla/firefox/test/chrome/userChrome.css".text; prefs=f.".config/mozilla/firefox/test/user.js".text; }}'''
    env = os.environ.copy()
    env['NP_RUNTIME'] = 'bwrap'
    evaluated = json.loads(subprocess.check_output([str(Path.home() / '.local/bin/nix-portable'), 'nix',
        'eval', '--offline', '--impure', '--json', '--expr', expression], cwd=ROOT, env=env, text=True, timeout=120))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-hm-profile-') as directory:
        profile = Path(directory)
        store = Path.home() / '.nix-portable' / evaluated['chrome'].lstrip('/')
        shutil.copytree(store, profile / 'chrome')
        (profile / 'chrome').chmod(0o755)
        (profile / 'chrome' / 'config.css').write_text(evaluated['config'])
        (profile / 'chrome' / 'userChrome.css').write_text(evaluated['entry'])
        (profile / 'user.js').write_text(evaluated['prefs'] + f'\nuser_pref("marionette.port", {port});\n')
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled')}", sandbox='system')
            if process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve() or meta['nova']:
                raise RuntimeError(f'unexpected Firefox process, profile, or Nova preference: {meta}')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox startup did not finish')
                time.sleep(.1)
            result = m.execute_script("""
                const root=getComputedStyle(document.documentElement), nav=getComputedStyle(document.getElementById('nav-bar'));
                return {configured:root.getPropertyValue('--tf-border-width').trim(), border:nav.borderTopWidth};
                """, sandbox='system')
            if result != {'configured':'7px', 'border':'7px'}:
                raise RuntimeError(f'Home Manager CSS not applied: {result}')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:preferences")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 20
            while not m.execute_script('return location.href.startsWith("about:preferences") && !!document.body'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('preferences page did not initialize')
                time.sleep(.1)
            font = m.execute_script('return getComputedStyle(document.body).fontFamily')
            if font != 'monospace':
                raise RuntimeError(f'Home Manager content font not applied: {font}')
            print(json.dumps({'firefox':meta, 'homeManagerFiles':result, 'preferencesFont':font}), flush=True)
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


if __name__ == '__main__':
    main()
