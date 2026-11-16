#!/usr/bin/env python3
"""Measure the collapsed vertical pinned-tabs container against the strip.

chrome/sidebar.css targets `#vertical-pinned-tabs-container`, absent from Firefox 159
live DOM. All three review agents confirmed earlier samples ran EXPANDED (`expanded`
attribute present), where the textfox `:not([expanded])` rule is inactive. This probe
collapses the launcher (updateUIState launcherExpanded:false), requires the strip to
lack the `expanded` attribute, then measures `#pinned-tabs-container` width against
the strip for 0/1/3 pins in both browser Nova states. Equal width means Firefox
already sizes it and the container branch can be dropped; narrower means the ID
should be swapped. Computed geometry in sampled Linux profiles, not painted output.
"""
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
VANILLA = bool(os.environ.get('TF_VANILLA'))


def run_case(nova, pins):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-pinned-vertical-') as directory:
        profile = Path(directory)
        if not VANILLA:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
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
            ready = m.execute_async_script("""
                const done=arguments[arguments.length-1];
                SidebarController.promiseInitialized.then(() => SidebarController.updateUIState({hidden:false, launcherExpanded:false}))
                  .then(() => done(true), e => done(String(e)));
                """, sandbox='system')
            if ready is not True:
                raise RuntimeError(f'sidebar state initialization failed: {ready}')
            deadline = time.monotonic() + 20
            while not m.execute_script("return !!gBrowser && document.querySelector('#tabbrowser-tabs')?.getAttribute('orient') === 'vertical'", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('vertical tab strip did not initialize')
                time.sleep(.1)
            deadline = time.monotonic() + 10
            while True:
                collapsed = m.execute_script("""
                    const t=document.getElementById('tabbrowser-tabs');
                    return !t.hasAttribute('expanded') && !!document.querySelector('box#vertical-tabs');
                    """, sandbox='system')
                if collapsed:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('strip did not reach the collapsed (no expanded attribute) state')
                time.sleep(.2)
            if pins:
                m.set_context('content')
                for i in range(pins):
                    m.open(f'data:text/html,<title>pin fixture {i}</title>')
                    time.sleep(.3)
                m.set_context('chrome')
                m.execute_script("""
                    for (const t of gBrowser.visibleTabs.slice(-arguments[0])) gBrowser.pinTab(t);
                    """, script_args=[pins], sandbox='system')
            deadline = time.monotonic() + 10
            while True:
                state = m.execute_script("""
                    const strip=document.getElementById('tabbrowser-tabs');
                    const old=document.getElementById('vertical-pinned-tabs-container');
                    const cur=document.getElementById('pinned-tabs-container');
                    const stripRect=strip.getBoundingClientRect();
                    const info=e=>{if(!e)return null;const s=getComputedStyle(e),r=e.getBoundingClientRect();
                      return {hasExpandedAttr:strip.hasAttribute('expanded'),
                        matchesTextfoxRule:e.matches('#tabbrowser-tabs[orient="vertical"]:not([expanded]) #'+e.id),
                        rectWidth:r.width,children:e.children.length,display:s.display};};
                    const pinStacks=[...strip.querySelectorAll('.tabbrowser-tab[pinned] .tab-stack')]
                      .map(e=>{const r=e.getBoundingClientRect();return {width:r.width,right:r.right};});
                    return {stripWidth:stripRect.width,stripRight:stripRect.right,
                      pinnedCount:strip.querySelectorAll('.tabbrowser-tab[pinned]').length,
                      old:old?{}:null,current:info(cur),pinStacks};
                    """, sandbox='system')
                if state and state['pinnedCount'] == pins:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'{pins} pins did not settle: {state}')
                time.sleep(.2)
            if state['old'] is not None:
                raise RuntimeError(f'stale vertical-pinned-tabs-container unexpectedly present: {state}')
            if state['current']['hasExpandedAttr']:
                raise RuntimeError(f'strip re-expanded before measurement: {state}')
            if len(state['pinStacks']) != pins or any(s['right'] > state['stripRight'] + 1 for s in state['pinStacks']):
                raise RuntimeError(f'pinned tab stacks overflow or missing: {state}')
            print(json.dumps({'nova': nova, 'pins': pins,
                              'firefox': {'version': m.execute_script('return Services.appinfo.version', sandbox='system'),
                                          'build': m.execute_script('return Services.appinfo.appBuildID', sandbox='system')},
                              'state': state,
                              'containerEqualsStrip': abs(state['current']['rectWidth'] - state['stripWidth']) <= 1}), flush=True)
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
        for pins in (0, 1, 3):
            run_case(nova, pins)
    print('Collapsed-state pinned-container measurement matrix completed')
