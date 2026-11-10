#!/usr/bin/env python3
"""Check split-panel fullscreen styling in disposable Firefox profiles."""
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(nova):
    selector_path=os.environ.get('TEXTFOX_SELECTOR_INVENTORY')
    selector_records=None
    selector_query=None
    if selector_path:
        selector_records=[r for r in json.loads(Path(selector_path).read_text()) if r['file']=='chrome/browser.css']
        if not selector_records:
            raise ValueError('selector inventory has no browser records')
        selector_query=runpy.run_path(str(ROOT / 'tools/firefox-chrome/probe-selector-dom.py'))['query']
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-split-fullscreen-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(
            ':root { --tf-border-width: 3px; --tf-display-titles: flex; --tf-accent: rgb(19, 83, 137); }\n')
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

            selector_states={}
            initial = snapshot()
            if selector_query:
                selector_states['initial']=selector_query(m,selector_records)
            panel=m.find_element('css selector','.split-view-panel.deck-selected')
            m.actions.sequence('pointer','mouse',{'pointerType':'mouse'}).pointer_move(0,0,origin=panel).perform()
            deadline=time.monotonic()+10
            while True:
                hover=m.execute_script("""
                    const e=document.querySelector('.split-view-panel.deck-selected'),s=getComputedStyle(e),p=getComputedStyle(e,'::before');
                    return {matches:e.matches(':hover'),border:s.borderTopColor,title:p.color};
                    """,sandbox='system')
                if hover['matches'] and hover['border']=='rgb(19, 83, 137)' and hover['title']=='rgb(19, 83, 137)':
                    break
                if time.monotonic()>=deadline:
                    raise RuntimeError(f'split panel pointer hover did not settle: {hover}')
                time.sleep(.1)
            if selector_query:
                selector_states['splitHover']=selector_query(m,selector_records)
                hover_rules=[r for r in selector_states['splitHover'] if r['selector']=='&:hover']
                if len(hover_rules)!=2 or any(r['status']!='structural-match' for r in hover_rules):
                    raise RuntimeError(f'split hover selector inventory inconclusive: {hover_rules}')
            m.execute_script('window.fullScreen = true', sandbox='system')
            deadline = time.monotonic() + 15
            while not m.execute_script("return window.fullScreen && document.documentElement.hasAttribute('inFullscreen')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox did not enter fullscreen')
                time.sleep(.1)
            fullscreen = snapshot()
            if selector_query:
                selector_states['fullscreen']=selector_query(m,selector_records)
            m.execute_script('window.fullScreen = false', sandbox='system')
            deadline = time.monotonic() + 15
            while m.execute_script("return window.fullScreen || document.documentElement.hasAttribute('inFullscreen')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox did not exit fullscreen')
                time.sleep(.1)
            restored = snapshot()
            m.set_context('content')
            m.navigate('data:text/html,<button id="fullscreen" onclick="document.documentElement.requestFullscreen()">fullscreen</button>')
            m.find_element('id', 'fullscreen').click()
            deadline = time.monotonic() + 15
            while not m.execute_script('return !!document.fullscreenElement'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('content did not enter DOM fullscreen')
                time.sleep(.1)
            m.set_context('chrome')
            deadline = time.monotonic() + 15
            while not m.execute_script("return document.documentElement.hasAttribute('inDOMFullscreen')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('browser did not enter DOM fullscreen state')
                time.sleep(.1)
            dom_fullscreen = snapshot()
            if selector_query:
                selector_states['domFullscreen']=selector_query(m,selector_records)
            dom_fullscreen['domAttribute'] = m.execute_script("return document.documentElement.getAttribute('inDOMFullscreen')", sandbox='system')
            m.set_context('content')
            m.execute_async_script("document.exitFullscreen().then(() => arguments[arguments.length-1](true))")
            m.set_context('chrome')
            deadline = time.monotonic() + 15
            while m.execute_script("return document.documentElement.hasAttribute('inDOMFullscreen')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('browser did not exit DOM fullscreen state')
                time.sleep(.1)
            dom_restored = snapshot()
            if selector_query:
                for name in ('initial','fullscreen','domFullscreen'):
                    candidates=[r for r in selector_states[name] if r['selector']==':root[inFullscreen="true"]']
                    expected='not-found-in-sampled-document' if name=='initial' else 'structural-match'
                    if len(candidates)!=1 or candidates[0]['status']!=expected:
                        raise RuntimeError(f'fullscreen selector inventory inconclusive: {name}, {candidates}')
            print(json.dumps({'firefox': metadata, 'initial': initial, 'splitHover':hover, 'fullscreen': fullscreen,
                              'restored': restored, 'domFullscreen':dom_fullscreen,
                              'domRestored':dom_restored,'selectorStates':selector_states}), flush=True)
            if dom_fullscreen['domAttribute'] != 'true':
                raise RuntimeError(f'DOM fullscreen attribute missing: {dom_fullscreen}')
            for state in (fullscreen, dom_fullscreen):
                if state['attribute'] != 'true' or len(state['panels']) != 2:
                    raise RuntimeError(f'fullscreen attribute or split panels differ: {state}')
                for panel in state['panels']:
                    if (panel['margin'] != '0px' or panel['borderWidth'] != '0px' or panel['padding'] != '0px'
                            or panel['outlineStyle'] != 'none' or panel['titleDisplay'] != 'none'):
                        raise RuntimeError(f'fullscreen split decoration remains: {panel}')
            for state in (initial, restored, dom_restored):
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
