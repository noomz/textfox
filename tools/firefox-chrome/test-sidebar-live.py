#!/usr/bin/env python3
"""Check that the bookmarks sidebar resize control remains available."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from marionette_driver.keys import Keys

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(nova, rtl, high_contrast):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-sidebar-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False,
                 'intl.l10n.pseudo': 'bidi' if rtl else '',
                 'ui.useAccessibilityTheme': 1 if high_contrast else 0}
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
            metadata = m.execute_script("return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, build:Services.appinfo.appBuildID, nova:Services.prefs.getBoolPref('browser.nova.enabled')}", sandbox='system')
            if process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve() or metadata['nova'] != nova:
                raise RuntimeError('unexpected Firefox process, profile, or Nova preference')
            m.execute_script('SidebarController.show("viewBookmarksSidebar")', sandbox='system')
            deadline = time.monotonic() + 20
            while not m.execute_script("return document.getElementById('sidebar-box').getAttribute('sidebarcommand') === 'viewBookmarksSidebar' && !document.getElementById('sidebar-box').hidden", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('bookmarks sidebar did not open')
                time.sleep(.2)
            result = m.execute_script("""
                const splitter = document.getElementById('sidebar-splitter');
                const style = getComputedStyle(splitter);
                const rect = splitter.getBoundingClientRect();
                splitter.focus();
                return {display:style.display, visibility:style.visibility, width:rect.width,
                        height:rect.height, focused:document.activeElement === splitter,
                        direction:getComputedStyle(document.documentElement).direction,
                        prefersContrast:matchMedia('(prefers-contrast)').matches,
                        forcedColors:matchMedia('(forced-colors: active)').matches};
                """, sandbox='system')
            print(json.dumps({'firefox': metadata, 'splitter': result}), flush=True)
            if result['display'] == 'none' or result['visibility'] != 'visible' or result['width'] <= 0 or result['height'] <= 0:
                raise RuntimeError('sidebar resize control is not visible')
            if not result['focused']:
                raise RuntimeError('sidebar resize control cannot receive focus')
            if result['direction'] != ('rtl' if rtl else 'ltr'):
                raise RuntimeError('requested document direction not active')
            if result['prefersContrast'] != high_contrast:
                raise RuntimeError('requested prefers-contrast state not active')
            before = m.execute_script("return document.getElementById('sidebar-box').getBoundingClientRect().width", sandbox='system')
            m.find_element('id', 'sidebar-splitter').send_keys(Keys.ARROW_LEFT if rtl else Keys.ARROW_RIGHT)
            deadline = time.monotonic() + 5
            while True:
                after = m.execute_script("return document.getElementById('sidebar-box').getBoundingClientRect().width", sandbox='system')
                if after > before:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'expand arrow did not expand sidebar: {before} -> {after}')
                time.sleep(.1)
            m.find_element('id', 'sidebar-splitter').send_keys(Keys.ARROW_RIGHT if rtl else Keys.ARROW_LEFT)
            deadline = time.monotonic() + 5
            while True:
                shrunk = m.execute_script("return document.getElementById('sidebar-box').getBoundingClientRect().width", sandbox='system')
                if shrunk < after:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'shrink arrow did not shrink sidebar: {after} -> {shrunk}')
                time.sleep(.1)
            focus = m.execute_script("""
                const splitter = document.getElementById('sidebar-splitter');
                const s = getComputedStyle(splitter);
                return {focusVisible:splitter.matches(':focus-visible'), outlineStyle:s.outlineStyle,
                        outlineWidth:s.outlineWidth, outlineColor:s.outlineColor};
                """, sandbox='system')
            print(json.dumps({'nova': nova, 'rtl': rtl, 'highContrast': high_contrast, 'sidebarWidthBefore': before,
                              'sidebarWidthExpanded': after, 'sidebarWidthShrunk': shrunk,
                              'focus': focus}), flush=True)
            if not focus['focusVisible'] or focus['outlineStyle'] == 'none' or focus['outlineWidth'] == '0px':
                raise RuntimeError('keyboard-focused splitter has no computed focus outline')
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
        for rtl in (False, True):
            for high_contrast in (False, True):
                check(nova, rtl, high_contrast)
