#!/usr/bin/env python3
"""Check configured font on selected Firefox about pages in disposable profiles."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

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
                result = m.execute_script("""
                    return {url:location.href, state:document.readyState,
                      body:!!document.body, font:document.body && getComputedStyle(document.body).fontFamily};
                    """)
                print(json.dumps({'firefox': metadata, 'requested': page, 'result': result}), flush=True)
                if result['url'].split('#')[0] != page or not result['body'] or result['font'] != 'monospace':
                    raise RuntimeError(f'about page font or navigation differs from configuration: {result}')
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
