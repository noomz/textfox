#!/usr/bin/env python3
"""Compare horizontal tab and window-control geometry in headed Firefox."""
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


def check(nova, mode):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-window-controls-') as directory:
        profile = Path(directory)
        if mode != 'vanilla':
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
            (profile / 'chrome' / 'config.css').write_text(
                f':root {{ --tf-display-horizontal-tabs: {mode}; --tf-display-window-controls: flex; }}\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'sidebar.verticalTabs': False,
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
                  nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  vertical:Services.prefs.getBoolPref('sidebar.verticalTabs')};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova or metadata['vertical']):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox startup did not finish')
                time.sleep(.1)
            result = m.execute_script("""
                const root = document.documentElement;
                const tab = document.querySelector('#TabsToolbar .tabbrowser-tab[selected]');
                const close = document.querySelector('#TabsToolbar .titlebar-buttonbox-container .titlebar-close');
                if (!tab || !close) throw new Error('tab or titlebar close button missing');
                const rect = element => { const r = element.getBoundingClientRect();
                  return {x:r.x, y:r.y, width:r.width, height:r.height}; };
                return {customTitlebar:root.hasAttribute('customtitlebar'),
                  toolbarDisplay:getComputedStyle(document.getElementById('TabsToolbar')).display,
                  closeDisplay:getComputedStyle(close).display,
                  tab:rect(tab), close:rect(close)};
                """, sandbox='system')
            if (not result['customTitlebar'] or result['closeDisplay'] == 'none'
                    or result['tab']['width'] <= 0 or result['tab']['height'] <= 0
                    or result['close']['width'] <= 0 or result['close']['height'] <= 0):
                raise RuntimeError(f'headed custom-titlebar fixture unavailable: {result}')
            tab, close = result['tab'], result['close']
            result['verticalIntersection'] = max(0, min(tab['y'] + tab['height'], close['y'] + close['height'])
                                                   - max(tab['y'], close['y']))
            print(json.dumps({'firefox': metadata, 'mode': mode, 'result': result}), flush=True)
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
        for mode in ('vanilla', 'block', 'flex'):
            check(nova, mode)
