#!/usr/bin/env python3
"""Inspect keyboard-active XUL menu items in disposable Firefox profiles."""
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


def check(nova, themed):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-menu-active-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
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
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,
                  version:Services.appinfo.version,build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled')};
                """, sandbox='system')
            if process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve() or metadata['nova'] != nova:
                raise RuntimeError('unexpected Firefox process, profile, or Nova preference')
            m.execute_script("""
                const popup = document.createXULElement('menupopup');
                popup.id = 'textfox-probe-menu';
                for (const label of ['First', 'Second']) {
                  const item = document.createXULElement('menuitem');
                  item.setAttribute('label', label);
                  popup.append(item);
                }
                document.getElementById('mainPopupSet').append(popup);
                popup.openPopup(document.getElementById('PanelUI-menu-button'), 'after_end');
                """, sandbox='system')
            deadline = time.monotonic() + 15
            while not m.execute_script("return document.getElementById('textfox-probe-menu').state === 'open'", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('fixture menu did not open')
                time.sleep(.1)
            m.actions.sequence('key', 'keyboard').send_keys(Keys.ARROW_DOWN).perform()
            deadline = time.monotonic() + 10
            while True:
                sample = m.execute_script("""
                    const menu = document.getElementById('textfox-probe-menu');
                    const active = menu.querySelector('menuitem[_moz-menuactive="true"]');
                    if (!active) return null;
                    const s = getComputedStyle(active), r = active.getBoundingClientRect();
                    const root = getComputedStyle(document.documentElement);
                    return {label:active.getAttribute('label'), background:s.backgroundColor,
                      color:s.color, width:r.width, height:r.height,
                      selectedToken:s.getPropertyValue('--tab-selected-bgcolor').trim(),
                      fieldColor:root.getPropertyValue('--toolbar-field-color').trim(),
                      toolbarColor:root.getPropertyValue('--toolbar-bgcolor').trim()};
                    """, sandbox='system')
                if sample:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('keyboard did not activate a menu item')
                time.sleep(.1)
            if sample['width'] <= 0 or sample['height'] <= 0:
                raise RuntimeError(f'active menu item has no geometry: {sample}')
            intervention = None
            if themed:
                intervention = m.execute_script("""
                    const active = document.querySelector('#textfox-probe-menu menuitem[_moz-menuactive="true"]');
                    active.style.setProperty('--tab-selected-bgcolor', 'Highlight', 'important');
                    const style = getComputedStyle(active);
                    const result = {background:style.backgroundColor, color:style.color,
                      selectedToken:style.getPropertyValue('--tab-selected-bgcolor').trim()};
                    active.style.removeProperty('--tab-selected-bgcolor');
                    return result;
                    """, sandbox='system')
            m.actions.sequence('key', 'keyboard').send_keys(Keys.ESCAPE).perform()
            deadline = time.monotonic() + 10
            while m.execute_script("return document.getElementById('textfox-probe-menu').state", sandbox='system') != 'closed':
                if time.monotonic() >= deadline:
                    raise RuntimeError('Escape did not close fixture menu')
                time.sleep(.1)
            print(json.dumps({'firefox':metadata, 'themed':themed, 'active':sample,
                              'intervention':intervention}), flush=True)
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
        for themed in (False, True):
            check(nova, themed)
