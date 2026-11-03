#!/usr/bin/env python3
"""Inspect Nova layout with search disabled in disposable Firefox profiles."""
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


def check(browser_nova, themed):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-no-search-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port': port, 'browser.nova.enabled': browser_nova,
                 'browser.newtabpage.activity-stream.nova.enabled': True,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
                 'browser.newtabpage.activity-stream.widgets.enabled': True,
                 'browser.newtabpage.activity-stream.showSearch': False,
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
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,browserNova:Services.prefs.getBoolPref('browser.nova.enabled'),newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled'),showSearch:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.showSearch')}", sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['browserNova'] != browser_nova or not meta['newtabNova'] or meta['showSearch']):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while not m.execute_script('return location.href === "about:newtab" && !!document.querySelector(".nova-enabled .top-site-outer")'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Nova shortcuts did not initialize')
                time.sleep(.25)
            result = m.execute_script("""
                const container = document.querySelector('.container.nova-enabled');
                const shortcut = document.querySelector('.nova-enabled .top-site-outer');
                const logo = document.querySelector('.nova-enabled .wordmark');
                const rect = e => {const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height};};
                return {search:!!container.querySelector('.search-wrapper'), centered:container.classList.contains('logo-in-content'),
                        shortcut:rect(shortcut), logo:logo ? rect(logo) : null,
                        shortcutVisible:getComputedStyle(shortcut).visibility, viewport:[innerWidth, innerHeight]};
                """)
            print(json.dumps({'firefox': meta, 'themed': themed, 'result': result}), flush=True)
            if (result['search'] or not result['centered'] or result['shortcutVisible'] != 'visible'
                    or result['shortcut']['width'] <= 0 or result['shortcut']['height'] <= 0):
                raise RuntimeError('search-disabled Nova layout did not show centered-logo shortcuts')
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
    for browser_nova in (False, True):
        for themed in (False, True):
            check(browser_nova, themed)
