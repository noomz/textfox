#!/usr/bin/env python3
"""Compare sidebar header controls in disposable Firefox profiles."""
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


def check(nova, revamp, themed):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-sidebar-header-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
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
            m.set_context('chrome')
            metadata = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path,
                  version:Services.appinfo.version, build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  revamp:Services.prefs.getBoolPref('sidebar.revamp')};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova or metadata['revamp'] != revamp):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            m.execute_script('SidebarController.show("viewBookmarksSidebar")', sandbox='system')
            deadline = time.monotonic() + 20
            while not m.execute_script("return document.getElementById('sidebar-box').getAttribute('sidebarcommand') === 'viewBookmarksSidebar' && !document.getElementById('sidebar-box').hidden", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('bookmarks sidebar did not open')
                time.sleep(.1)
            result = m.execute_script("""
                const ids=['sidebar-header','sidebar-close','sidebar-switcher-target'];
                return Object.fromEntries(ids.map(id => {
                  const e=document.getElementById(id);
                  if (!e) throw new Error(`missing ${id}`);
                  const s=getComputedStyle(e), r=e.getBoundingClientRect();
                  return [id,{display:s.display, visibility:s.visibility, width:r.width, height:r.height}];
                }));
                """, sandbox='system')
            for item in result.values():
                item['hasGeometry'] = item['display'] != 'none' and item['visibility'] == 'visible' and item['width'] > 0 and item['height'] > 0
            intervention = None
            if themed and not revamp:
                intervention = m.execute_script("""
                    const header = document.getElementById('sidebar-header');
                    header.style.setProperty('display', 'flex', 'important');
                    const ids=['sidebar-header','sidebar-close','sidebar-switcher-target'];
                    const measured=Object.fromEntries(ids.map(id => {
                      const r=document.getElementById(id).getBoundingClientRect();
                      return [id,{width:r.width,height:r.height}];
                    }));
                    header.style.removeProperty('display');
                    return measured;
                    """, sandbox='system')
            print(json.dumps({'firefox': metadata, 'themed': themed, 'result': result,
                              'headerIntervention': intervention}), flush=True)
            if not revamp and not themed and not all(item['hasGeometry'] for item in result.values()):
                raise RuntimeError(f'classic vanilla header positive control is not visible: {result}')
            m.actions.sequence('key', 'keyboard').key_down(Keys.CONTROL).key_down('b').key_up('b').key_up(Keys.CONTROL).perform()
            deadline = time.monotonic() + 10
            while not m.execute_script("return document.getElementById('sidebar-box').hidden", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Ctrl+B did not close bookmarks sidebar')
                time.sleep(.1)
            print(json.dumps({'nova': nova, 'revamp': revamp, 'themed': themed,
                              'keyboardClosedSidebar': True}), flush=True)
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
        for revamp in (False, True):
            for themed in (False, True):
                check(nova, revamp, themed)
