#!/usr/bin/env python3
"""Check Nix-generated custom logo CSS in disposable Firefox new-tab content."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
NIXPKGS = '/nix/store/771lrblshx04rd5hgbg69pxkfzm9ncqh-source'


def main():
    expression = f'import ./tools/firefox-chrome/check-nixos-eval.nix {{ nixpkgsPath = {NIXPKGS}; }}'
    generated = json.loads(subprocess.check_output(
        ['nix', 'eval', '--offline', '--impure', '--json', '--expr', expression], cwd=ROOT, text=True, timeout=120))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-nix-logo-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(generated['generatedCss'])
        prefs = {'marionette.port': port, 'browser.nova.enabled': True,
                 'browser.newtabpage.activity-stream.nova.enabled': True,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
                 'browser.newtabpage.activity-stream.widgets.enabled': True,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,browserNova:Services.prefs.getBoolPref('browser.nova.enabled'),newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled')}", sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or not meta['browserNova'] or not meta['newtabNova']):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while not m.execute_script('return location.href === "about:newtab" && !!document.querySelector(".nova-enabled .wordmark")'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Nova wordmark did not initialize')
                time.sleep(.25)
            result = m.execute_script("""
                const p = getComputedStyle(document.querySelector('.nova-enabled .wordmark'), '::before');
                return {content:p.content, border:getComputedStyle(document.documentElement).getPropertyValue('--tf-border-width').trim()};
                """)
            print(json.dumps({'firefox':meta, 'result':result}), flush=True)
            if result['content'] != '"say \\"hi\\"\\\\path\\a 1next\\d test\\a "':
                raise RuntimeError('Nix-generated logo does not preserve quotes, backslashes, and newlines')
            if result['border'] != '7px':
                raise RuntimeError('Nix-generated CSS did not apply')
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
