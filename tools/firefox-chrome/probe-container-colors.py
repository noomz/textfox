#!/usr/bin/env python3
"""Inspect Firefox container tab color variables in disposable profiles."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(nova, themed, vertical):
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
            if (len(result) < 4 or any(item['width'] <= 0 or item['height'] <= 0
                    or f"identity-color-{item['color']}" not in item['classes'].split()
                    or not item['tabColor'] or not item['iconColor'] or item['lineDisplay'] is None
                    for item in result)):
                raise RuntimeError(f'container tab fixture unavailable: {result}')
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
                              'selectedPinned':selected_pinned}), flush=True)
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
