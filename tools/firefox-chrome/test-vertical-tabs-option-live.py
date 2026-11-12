#!/usr/bin/env python3
"""Check --tf-display-vertical-tabs and textfox.config.tabs.vertical.enable."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def run(nova, config_css):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-vertical-tabs-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        if config_css is not None:
            (profile / 'chrome' / 'config.css').write_text(config_css)
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'sidebar.revamp': True, 'sidebar.verticalTabs': True,
                 'sidebar.visibility': 'always-show',
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
            metadata = m.execute_script(
                "return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, "
                "version:Services.appinfo.version, build:Services.appinfo.appBuildID, "
                "nova:Services.prefs.getBoolPref('browser.nova.enabled')}", sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova):
                raise RuntimeError('unexpected Firefox process, profile, or nova preference')
            ready = m.execute_async_script("""
                const done = arguments[arguments.length - 1];
                SidebarController.promiseInitialized
                  .then(() => SidebarController.updateUIState({hidden:false, launcherExpanded:true}))
                  .then(() => done(true), e => done(String(e)));
                """, sandbox='system')
            if ready is not True:
                raise RuntimeError(f'sidebar initialization failed: {ready}')
            deadline = time.monotonic() + 20
            while not m.execute_script(
                    "return !!document.querySelector('box#vertical-tabs')?.querySelector('#tabbrowser-tabs')",
                    sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('vertical tab strip did not initialize')
                time.sleep(.2)
            result = m.execute_script("""
                const root = getComputedStyle(document.documentElement);
                const strip = document.querySelector('box#vertical-tabs');
                const style = getComputedStyle(strip), rect = strip.getBoundingClientRect();
                const tabs = document.getElementById('tabbrowser-tabs');
                return {option:root.getPropertyValue('--tf-display-vertical-tabs').trim(),
                        display:style.display, width:rect.width, height:rect.height,
                        tabsPresent:!!tabs, tabCount:gBrowser.tabs.length,
                        horizontalOption:root.getPropertyValue('--tf-display-horizontal-tabs').trim()};
                """, sandbox='system')
            return metadata, result
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


def main():
    failures = []
    cases = [(nova, None, 'flex') for nova in (False, True)]
    cases += [(nova, css, expected)
              for nova in (False, True)
              for css, expected in (
                  (':root { --tf-display-vertical-tabs: none; }\n', 'none'),
                  (':root { --tf-display-vertical-tabs: flex; }\n', 'flex'))]
    for nova, css, expected in cases:
        try:
            metadata, result = run(nova, css)
        except Exception as error:
            failures.append(f'nova={nova} config={css!r}: {error}')
            print(json.dumps({'nova': nova, 'config': css, 'error': str(error)}), flush=True)
            continue
        print(json.dumps({'firefox': metadata, 'nova': nova, 'config': css, 'result': result}), flush=True)
        if result['option'] != expected:
            failures.append(f'nova={nova}: option reads {result["option"]!r}, expected {expected!r}')
        if result['display'] != expected:
            failures.append(f'nova={nova}: strip display {result["display"]!r}, expected {expected!r}')
        if not result['tabsPresent'] or result['tabCount'] < 1:
            failures.append(f'nova={nova}: tab strip disappeared')
        if expected == 'flex' and (result['width'] <= 0 or result['height'] <= 0):
            failures.append(f'nova={nova}: shown strip has no box')
        if expected == 'none' and (result['width'] != 0 or result['height'] != 0):
            failures.append(f'nova={nova}: hidden strip still occupies space')
        if result['horizontalOption'] != 'none':
            failures.append(f'nova={nova}: horizontal tab default drifted')
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', file=sys.stderr)
        sys.exit(1)
    print(f'vertical tabs option holds for {len(cases)} cases')


if __name__ == '__main__':
    main()
