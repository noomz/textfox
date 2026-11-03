#!/usr/bin/env python3
"""Check private-window URL bar results and configured borders in Firefox."""
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
    with tempfile.TemporaryDirectory(prefix='tf-private-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(
            ':root { --tf-accent: rgb(19, 83, 137); --tf-border-width: 3px; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile),
                                    '--private-window', 'about:blank'], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled'),private:PrivateBrowsingUtils.isWindowPrivate(window)}", sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['nova'] != nova or not meta['private']):
                raise RuntimeError(f'unexpected Firefox process, profile, or window: {meta}')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('private window startup did not finish')
                time.sleep(.1)
            m.execute_script("gURLBar.focus(); gURLBar.search('mozilla')", sandbox='system')
            deadline = time.monotonic() + 20
            while not m.execute_script("return !!document.querySelector('#urlbar[open] .urlbarView-results .urlbarView-row')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('private URL bar results did not open')
                time.sleep(.1)
            result = m.execute_script("""
                const view = document.querySelector('#urlbar[open] .urlbarView');
                const results = document.querySelector('#urlbar[open] > .urlbarView > .urlbarView-results');
                if (!results) throw new Error('URL bar results missing');
                const v = getComputedStyle(view), r = getComputedStyle(results);
                return {rows:results.querySelectorAll('.urlbarView-row').length,
                        borderWidth:v.borderTopWidth, borderStyle:v.borderTopStyle,
                        borderColor:v.borderTopColor, separatorWidth:r.borderTopWidth};
                """, sandbox='system')
            print(json.dumps({'firefox': meta, 'result': result}), flush=True)
            if (result['borderWidth'] != '3px' or result['borderStyle'] != 'solid'
                    or result['borderColor'] != 'rgb(19, 83, 137)' or result['separatorWidth'] != '0px'):
                raise RuntimeError('private URL bar does not preserve configured result borders')
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
