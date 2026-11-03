#!/usr/bin/env python3
"""Check the sidebar tools visibility option in disposable Firefox profiles."""
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


def check(nova, display):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-sidebar-tools-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(f':root {{ --tf-display-sidebar-tools: {display}; }}\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'sidebar.revamp': True, 'sidebar.verticalTabs': True,
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
            metadata = m.execute_script("return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, build:Services.appinfo.appBuildID, nova:Services.prefs.getBoolPref('browser.nova.enabled'), verticalTabs:Services.prefs.getBoolPref('sidebar.verticalTabs'), revamp:Services.prefs.getBoolPref('sidebar.revamp')}", sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova or not metadata['verticalTabs'] or not metadata['revamp']):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            deadline = time.monotonic() + 20
            while not m.execute_script("return !!document.querySelector('sidebar-main')?.shadowRoot?.querySelector('.buttons-wrapper')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('sidebar tools did not initialize')
                time.sleep(.2)
            result = m.execute_script("""
                const host = document.querySelector('sidebar-main');
                const wrapper = host.shadowRoot.querySelector('.buttons-wrapper');
                const s = getComputedStyle(wrapper), rect = wrapper.getBoundingClientRect();
                return {rootOption:getComputedStyle(document.documentElement).getPropertyValue('--tf-display-sidebar-tools').trim(),
                        display:s.display, visibility:s.visibility, width:rect.width, height:rect.height,
                        part:wrapper.getAttribute('part'), hostHidden:host.hidden,
                        lightDomMatches:document.querySelectorAll('.buttons-wrapper').length,
                        buttons:wrapper.querySelectorAll('moz-button').length};
                """, sandbox='system')
            if result['rootOption'] != display:
                raise RuntimeError('requested sidebar option not applied')
            print(json.dumps({'firefox': metadata, 'requested': display, 'result': result}), flush=True)
            if result['hostHidden'] or result['buttons'] == 0:
                raise RuntimeError('sidebar tools fixture is not available')
            if result['display'] != display:
                raise RuntimeError('sidebar tools display does not follow configuration')
            if display == 'flex' and (result['visibility'] != 'visible' or result['width'] <= 0 or result['height'] <= 0):
                raise RuntimeError('configured sidebar tools are not visible')
            if display == 'none' and (result['width'] != 0 or result['height'] != 0):
                raise RuntimeError('hidden sidebar tools still occupy space')
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
        for display in ('flex', 'none'):
            check(nova, display)
