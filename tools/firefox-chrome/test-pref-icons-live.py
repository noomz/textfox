#!/usr/bin/env python3
"""Verify production conditional rules follow effective preferences in live Firefox.

B2 regression check: the legacy `(-moz-bool-pref: ...)` media form never matched on
Firefox 159, silently disabling every shyfox icon gate and the sidebar.revamp navbar
gate. Production CSS now uses `@media -moz-pref(...)`. This test asserts computed
effects of production rules track the effective preference values, in both browser
Nova states, with the context menu actually open. Computed styles only; painting is
verified separately.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import quote

from marionette_driver.keys import Keys

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def run_case(nova, icons, revamp):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-pref-icons-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port': port,
                 'browser.nova.enabled': nova,
                 'shyfox.enable.context.menu.icons': icons,
                 'sidebar.revamp': revamp,
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
            m.set_context('content')
            page = '<a href="https://example.invalid/">textfox link fixture</a>'
            m.navigate('data:text/html,' + quote(page))
            link = m.find_element('css selector', 'a')
            m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(link, button=2).perform()
            m.set_context('chrome')
            deadline = time.monotonic() + 15
            while True:
                result = m.execute_script("""
                    const popup=document.getElementById('contentAreaContextMenu');
                    const priv=document.getElementById('context-openlinkprivate');
                    if (popup.state !== 'open' || !priv || priv.hidden) return null;
                    const r=priv.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) return null;
                    const q=(s)=>getComputedStyle(document.querySelector(s));
                    const openlink=q('#context-openlink'), private=q('#context-openlinkprivate');
                    const spacer=q('.titlebar-spacer[type="pre-tabs"]');
                    return {version:Services.appinfo.version, build:Services.appinfo.appBuildID,
                      prefs:{nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                        icons:Services.prefs.getBoolPref('shyfox.enable.context.menu.icons'),
                        revamp:Services.prefs.getBoolPref('sidebar.revamp')},
                      media:{icons:matchMedia('-moz-pref("shyfox.enable.context.menu.icons")').matches,
                        legacyIcons:matchMedia('(-moz-bool-pref: "shyfox.enable.context.menu.icons")').matches,
                        revamp:matchMedia('-moz-pref("sidebar.revamp")').matches},
                      images:{openlink:openlink.backgroundImage, privateLink:private.backgroundImage},
                      spacerWidth:spacer.width};
                    """, sandbox='system')
                if result:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('link context menu did not initialize')
                time.sleep(.1)
            m.actions.sequence('key', 'keyboard').send_keys(Keys.ESCAPE).perform()
            if process.poll() is not None:
                raise RuntimeError('Firefox exited during probe')
            if result['prefs'] != {'nova': nova, 'icons': icons, 'revamp': revamp}:
                raise RuntimeError(f'effective preferences diverged: {result["prefs"]}')
            if result['media']['icons'] != icons or result['media']['revamp'] != revamp:
                raise RuntimeError(f'modern media queries diverged: {result["media"]}')
            private_expected = 'url("chrome://browser/skin/privateBrowsing.svg")' if icons else 'none'
            if result['images']['privateLink'] != private_expected:
                raise RuntimeError(f'production private-tab icon gate failed: {result}')
            if icons:
                if 'icons/window.svg' not in result['images']['openlink']:
                    raise RuntimeError(f'production relative-asset icon gate failed: {result}')
            elif result['images']['openlink'] != 'none':
                raise RuntimeError(f'production icon leaked with preference off: {result}')
            if (result['spacerWidth'] == '0px') != revamp:
                raise RuntimeError(f'production sidebar.revamp navbar gate failed: {result}')
            print(json.dumps({'nova': nova, 'icons': icons, 'revamp': revamp,
                              'firefox': result['firefox'] if 'firefox' in result else
                              {'version': result['version'], 'build': result['build']},
                              'images': result['images'], 'spacerWidth': result['spacerWidth'],
                              'media': result['media']}), flush=True)
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
        for icons in (False, True):
            for revamp in (False, True):
                run_case(nova, icons, revamp)
    print('Preference-gated production rules follow effective preferences in 8 live cases')
