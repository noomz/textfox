#!/usr/bin/env python3
"""Check configured font on selected Firefox about pages in disposable profiles."""
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
PAGES = ('about:preferences', 'about:config', 'about:profiles', 'about:addons', 'about:support')


def check(nova):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-about-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(':root { --tf-font-family: monospace; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
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
            metadata = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path,
                  version:Services.appinfo.version, build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled')};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova):
                raise RuntimeError('unexpected Firefox process, profile, or Nova preference')
            m.set_context('content')
            for page in PAGES:
                m.navigate(page)
                if page == 'about:preferences':
                    deadline = time.monotonic() + 20
                    while not m.execute_script("return [...document.querySelectorAll('*')].some(e => e.localName === 'moz-page-nav-button')"):
                        if time.monotonic() >= deadline:
                            raise RuntimeError('about:preferences navigation controls did not initialize')
                        time.sleep(.1)
                result = m.execute_script("""
                    const names = new Set(['button', 'input', 'select', 'textarea', 'checkbox', 'menulist', 'moz-button', 'moz-page-nav-button']);
                    const controls = [...document.querySelectorAll('*')].filter(node => names.has(node.localName));
                    return {url:location.href, state:document.readyState,
                      body:!!document.body, font:document.body && getComputedStyle(document.body).fontFamily,
                      controls:controls.length,
                      differingControls:controls.filter(node => getComputedStyle(node).fontFamily !== 'monospace')
                        .map(node => ({tag:node.localName, id:node.id, font:getComputedStyle(node).fontFamily}))};
                    """)
                print(json.dumps({'firefox': metadata, 'requested': page, 'result': result}), flush=True)
                if (result['url'].split('#')[0] != page or not result['body'] or result['font'] != 'monospace'
                        or result['differingControls']):
                    raise RuntimeError(f'about page font or navigation differs from configuration: {result}')
                if result['controls'] == 0:
                    raise RuntimeError(f'{page} control fixture is empty')
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
    for nova in (False, True):
        check(nova)
