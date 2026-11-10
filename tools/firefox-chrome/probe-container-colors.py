#!/usr/bin/env python3
"""Inspect Firefox container tab color variables in disposable profiles."""
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import tempfile

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(nova, themed, vertical):
    selector_path=os.environ.get('TEXTFOX_SELECTOR_INVENTORY')
    selector_records=None
    selector_query=None
    if selector_path:
        selector_records=[r for r in json.loads(Path(selector_path).read_text()) if r['file']=='chrome/overwrites.css']
        if not selector_records:
            raise ValueError('selector inventory has no overwrite records')
        selector_query=runpy.run_path(str(ROOT / 'tools/firefox-chrome/probe-selector-dom.py'))['query']
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-container-colors-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
            (profile / 'chrome' / 'config.css').write_text(':root { --tf-display-horizontal-tabs: block; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'sidebar.verticalTabs': vertical, 'privacy.userContext.enabled': True,
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
            meta = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,
                  version:Services.appinfo.version, build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  vertical:Services.prefs.getBoolPref('sidebar.verticalTabs'),
                  containers:Services.prefs.getBoolPref('privacy.userContext.enabled')};
                """, sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['nova'] != nova or meta['vertical'] != vertical or not meta['containers']):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            result = m.execute_async_script("""
                const done=arguments[arguments.length-1];
                const {ContextualIdentityService} = ChromeUtils.importESModule('moz-src:///toolkit/components/contextualidentity/ContextualIdentityService.sys.mjs');
                const extraColors=['cyan','yellow','red','purple','violet','gray'];
                for (const color of extraColors) ContextualIdentityService.create(`textfox ${color}`, 'fingerprint', color);
                const identities=ContextualIdentityService.getPublicIdentities();
                const tabs=identities.map(identity => ({identity,tab:gBrowser.addTrustedTab('about:blank', {userContextId:identity.userContextId})}));
                requestAnimationFrame(() => requestAnimationFrame(() => done(tabs.map(({identity,tab}) => {
                  const line=tab.querySelector('.tab-context-line'), s=getComputedStyle(tab);
                  const ls=line && getComputedStyle(line), r=tab.getBoundingClientRect();
                  return {id:identity.userContextId, color:identity.color, classes:tab.className,
                    dataColor:tab.getAttribute('data-color'), width:r.width,height:r.height,
                    tabColor:s.getPropertyValue('--identity-tab-color').trim(),
                    iconColor:s.getPropertyValue('--identity-icon-color').trim(),
                    strokeColor:s.getPropertyValue('--identity-stroke-color').trim(),
                    lineColor:ls?.backgroundColor, lineImage:ls?.backgroundImage, lineDisplay:ls?.display};
                }))));
                """, sandbox='system')
            expected_colors = {'blue', 'orange', 'green', 'pink', 'cyan', 'yellow', 'red', 'purple', 'violet', 'gray'}
            if (not expected_colors.issubset({item['color'] for item in result})
                    or any(item['width'] <= 0 or item['height'] <= 0
                    or f"identity-color-{item['color']}" not in item['classes'].split()
                    or not item['tabColor'] or not item['iconColor'] or item['lineDisplay'] is None
                    for item in result)):
                raise RuntimeError(f'container tab fixture unavailable: {result}')
            selector_states={}
            turquoise_control=None
            if selector_query:
                selector_states['realIdentities']=selector_query(m,selector_records)
                for color in ('yellow','purple','blue','green','orange','red','pink'):
                    candidates=[r for r in selector_states['realIdentities'] if f'[data-color="{color}"]' in r['selector']]
                    if len(candidates)!=1 or candidates[0]['status']!='structural-match':
                        raise RuntimeError(f'container selector did not match real identity {color}: {candidates}')
                turquoise=m.execute_script("""
                    const e=document.createXULElement('box');
                    e.setAttribute('data-color','turquoise');e.setAttribute('data-textfox-turquoise-probe','true');
                    document.getElementById('mainPopupSet').append(e);
                    const s=getComputedStyle(e);
                    return {color:s.getPropertyValue('--color').trim(),tab:s.getPropertyValue('--identity-tab-color').trim(),
                      icon:s.getPropertyValue('--identity-icon-color').trim()};
                    """,sandbox='system')
                try:
                    selector_states['turquoiseControl']=selector_query(m,selector_records)
                    candidate=[r for r in selector_states['turquoiseControl'] if '[data-color="turquoise"]' in r['selector']]
                    if len(candidate)!=1 or candidate[0]['status']!='structural-match':
                        raise RuntimeError(f'turquoise selector control failed: {candidate}')
                    if themed and (not turquoise['color'] or turquoise['color']!=turquoise['tab'] or turquoise['color']!=turquoise['icon']):
                        raise RuntimeError(f'turquoise variable control failed: {turquoise}')
                    turquoise_control=turquoise
                finally:
                    m.execute_script("document.querySelector('[data-textfox-turquoise-probe]')?.remove()",sandbox='system')
            selected_pinned = m.execute_async_script("""
                const done=arguments[arguments.length-1];
                const tab=[...gBrowser.tabs].find(t => t.userContextId === arguments[0]);
                if (!tab) { done({error:'container tab not found'}); return; }
                gBrowser.selectedTab=tab;
                requestAnimationFrame(() => requestAnimationFrame(() => {
                  const sample=()=>{const line=tab.querySelector('.tab-context-line'), s=getComputedStyle(line);
                    const r=line.getBoundingClientRect(); return {selected:tab.selected,pinned:tab.pinned,
                      color:s.backgroundColor,image:s.backgroundImage,display:s.display,width:r.width,height:r.height};};
                  const selected=sample();
                  gBrowser.pinTab(tab);
                  requestAnimationFrame(() => requestAnimationFrame(() => done({selected,pinned:sample()})));
                }));
                """, script_args=[result[0]['id']], sandbox='system')
            if (selected_pinned.get('error') or not selected_pinned['selected']['selected']
                    or selected_pinned['selected']['pinned'] or not selected_pinned['pinned']['pinned']
                    or not selected_pinned['pinned']['selected']
                    or any(state['display'] == 'none' or state['width'] <= 0 or state['height'] <= 0
                           for state in selected_pinned.values())):
                raise RuntimeError(f'container selected/pinned fixture failed: {selected_pinned}')
            print(json.dumps({'firefox':meta, 'themed':themed, 'tabs':result,
                              'selectedPinned':selected_pinned,'selectorStates':selector_states,
                              'turquoiseControl':turquoise_control}), flush=True)
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
            for vertical in (False, True):
                check(nova, themed, vertical)
