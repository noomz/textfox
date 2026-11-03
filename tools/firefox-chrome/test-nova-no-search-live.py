#!/usr/bin/env python3
"""Inspect Nova layout with search disabled in disposable Firefox profiles.

Requires ImageMagick. Disposable new-tab feeds may make network requests.
"""
import json
import os
import subprocess
from pathlib import Path
import shutil
import socket
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
                        logoDisplay:logo ? getComputedStyle(logo).display : null,
                        shortcutVisible:getComputedStyle(shortcut).visibility, viewport:[innerWidth, innerHeight]};
                """)
            if themed and result['logo'] and result['logo']['width'] > 0:
                first, control, hidden = (profile / name for name in ('first.png', 'control.png', 'hidden.png'))
                first.write_bytes(m.screenshot(format='binary', full=False))
                control.write_bytes(m.screenshot(format='binary', full=False))
                m.execute_script("""
                    const style = document.createElement('style');
                    style.textContent = '.nova-enabled .wordmark::before { color: transparent !important; }';
                    document.head.appendChild(style);
                    """)
                hidden.write_bytes(m.screenshot(format='binary', full=False))
                r = result['shortcut']
                crop = f"{int(r['width'])}x{int(r['height'])}+{int(r['x'])}+{int(r['y'])}"
                def changed(a, b):
                    return round(float(subprocess.check_output(['magick', str(a), str(b), '-crop', crop, '+repage',
                        '-compose', 'difference', '-composite', '-colorspace', 'gray', '-threshold', '0',
                        '-format', '%[fx:mean*w*h]', 'info:'], text=True)))
                result['shortcutControlPixels'] = changed(first, control)
                result['shortcutLogoPixels'] = changed(control, hidden)
                def full_changed(a, b):
                    return round(float(subprocess.check_output(['magick', str(a), str(b),
                        '-compose', 'difference', '-composite', '-colorspace', 'gray', '-threshold', '0',
                        '-format', '%[fx:mean*w*h]', 'info:'], text=True)))
                result['fullLogoPixels'] = full_changed(control, hidden)
                if result['logoDisplay'] != 'none' and result['fullLogoPixels'] == 0:
                    raise RuntimeError('logo transparency intervention changed no pixels')
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
