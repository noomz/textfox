#!/usr/bin/env python3
"""Check split-panel fullscreen styling in disposable Firefox profiles."""
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


def check(nova):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-split-fullscreen-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(
            ':root { --tf-border-width: 3px; --tf-display-titles: flex; }\n')
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
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox startup did not finish')
                time.sleep(.1)
            m.execute_script("""
                const first = gBrowser.selectedTab, second = gBrowser.addTrustedTab('about:blank');
                gBrowser.addTabSplitView([first, second], {insertBefore:first});
                """, sandbox='system')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowser.tabpanels.splitViewPanels.length === 2', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('split view did not initialize')
                time.sleep(.1)

            def snapshot():
                return m.execute_script("""
                    const root=document.documentElement;
                    return {fullscreen:window.fullScreen, attribute:root.getAttribute('inFullscreen'),
                      panels:gBrowser.tabpanels.splitViewPanels.map(id => {
                        const panel=document.getElementById(id), style=getComputedStyle(panel);
                        return {selected:panel.classList.contains('deck-selected'), margin:style.margin,
                          borderWidth:style.borderTopWidth, padding:style.padding,
                          outlineStyle:style.outlineStyle,
                          titleDisplay:getComputedStyle(panel, '::before').display};
                      })};
                    """, sandbox='system')

            initial = snapshot()
            m.execute_script('window.fullScreen = true', sandbox='system')
            deadline = time.monotonic() + 15
            while not m.execute_script("return window.fullScreen && document.documentElement.hasAttribute('inFullscreen')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox did not enter fullscreen')
                time.sleep(.1)
            fullscreen = snapshot()
            m.execute_script('window.fullScreen = false', sandbox='system')
            deadline = time.monotonic() + 15
            while m.execute_script("return window.fullScreen || document.documentElement.hasAttribute('inFullscreen')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox did not exit fullscreen')
                time.sleep(.1)
            restored = snapshot()
            print(json.dumps({'firefox': metadata, 'initial': initial, 'fullscreen': fullscreen,
                              'restored': restored}), flush=True)
            if fullscreen['attribute'] != 'true' or len(fullscreen['panels']) != 2:
                raise RuntimeError(f'fullscreen attribute or split panels differ: {fullscreen}')
            for panel in fullscreen['panels']:
                if (panel['margin'] != '0px' or panel['borderWidth'] != '0px' or panel['padding'] != '0px'
                        or panel['outlineStyle'] != 'none' or panel['titleDisplay'] != 'none'):
                    raise RuntimeError(f'fullscreen split decoration remains: {panel}')
            for state in (initial, restored):
                if state['fullscreen'] or state['attribute'] is not None or len(state['panels']) != 2:
                    raise RuntimeError(f'windowed split state is invalid: {state}')
                if any(p['borderWidth'] != '3px' or p['padding'] != '8px' or p['titleDisplay'] != 'flex'
                       for p in state['panels']):
                    raise RuntimeError(f'windowed split decoration is not restored: {state}')
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
