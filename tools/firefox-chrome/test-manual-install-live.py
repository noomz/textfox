#!/usr/bin/env python3
"""Exercise manual installation in fresh disposable Firefox profiles."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(install_user_js):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-manual-install-') as directory:
        profile = Path(directory)
        marker = profile / 'unrelated.txt'
        marker.write_text('preserve this profile file')
        if not install_user_js:
            (profile / 'user.js').write_text('user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);\n')
        install = subprocess.run(['bash', str(ROOT / 'tf-install.sh'), str(profile)], cwd=ROOT,
                                 input='y\n' if install_user_js else 'n\n',
                                 capture_output=True, text=True, timeout=30)
        if install.returncode != 0 or 'Installation completed' not in install.stdout:
            raise RuntimeError(f'manual installer failed: {install.stdout} {install.stderr}')
        if marker.read_text() != 'preserve this profile file':
            raise RuntimeError('manual installer changed unrelated profile file')
        if not install_user_js and (profile / 'user.js').read_text() != 'user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);\n':
            raise RuntimeError('skipping user.js changed the existing file')
        if not (profile / 'chrome' / 'icons').is_dir():
            raise RuntimeError('manual installation did not copy icons')
        (profile / 'chrome' / 'config.css').write_text(
            ':root { --tf-accent: rgb(19, 83, 137); --tf-border-width: 3px; '
            '--tf-font-family: monospace; }\n')
        with (profile / 'user.js').open('a') as prefs:
            prefs.write(f'user_pref("marionette.port", {port});\n')
            prefs.write('user_pref("browser.shell.checkDefaultBrowser", false);\n')
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled'),newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled'),novaUser:Services.prefs.prefHasUserValue('browser.nova.enabled'),novaDefault:Services.prefs.getDefaultBranch('').getBoolPref('browser.nova.enabled')}", sandbox='system')
            if process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve():
                raise RuntimeError('unexpected Firefox process or profile')
            if install_user_js and (meta['nova'] or not meta['novaUser']):
                raise RuntimeError(f'installed user.js did not disable Nova: {meta}')
            if not install_user_js and (meta['novaUser'] or meta['nova'] != meta['novaDefault']):
                raise RuntimeError(f'skipping user.js unexpectedly changed Nova: {meta}')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox startup did not finish')
                time.sleep(.1)
            m.execute_script("gURLBar.focus(); gURLBar.search('mozilla')", sandbox='system')
            deadline = time.monotonic() + 20
            while not m.execute_script("return !!document.querySelector('#urlbar[open] .urlbarView-results .urlbarView-row')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('manual-install URL bar results did not open')
                time.sleep(.1)
            result = m.execute_script("""
                const s = getComputedStyle(document.querySelector('#urlbar[open] .urlbarView'));
                return {borderWidth:s.borderTopWidth, borderColor:s.borderTopColor};
                """, sandbox='system')
            print(json.dumps({'installedUserJs': install_user_js, 'firefox': meta, 'result': result}), flush=True)
            if result != {'borderWidth': '3px', 'borderColor': 'rgb(19, 83, 137)'}:
                raise RuntimeError('manual installation did not honor configured URL bar styles')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:preferences")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 20
            while not m.execute_script('return location.href.startsWith("about:preferences") && !!document.querySelector("body")'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('preferences page did not load')
                time.sleep(.1)
            font = m.execute_script("return getComputedStyle(document.body).fontFamily")
            if font != 'monospace':
                raise RuntimeError(f'manually installed about-page font not applied: {font}')
            print(json.dumps({'installedUserJs': install_user_js, 'aboutPreferencesFont': font}), flush=True)
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
    for install_user_js in (False, True):
        check(install_user_js)
