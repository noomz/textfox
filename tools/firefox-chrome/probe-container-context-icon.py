#!/usr/bin/env python3
"""Inspect the link context icon target inside a real disposable container tab."""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time

from marionette_driver.keys import Keys

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(nova, icons):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-container-context-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port':port, 'browser.nova.enabled':nova,
                 'shyfox.enable.context.menu.icons':icons,
                 'toolkit.legacyUserProfileCustomizations.stylesheets':True,
                 'browser.shell.checkDefaultBrowser':False}
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
                  version:Services.appinfo.version,build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  iconPref:Services.prefs.getBoolPref('shyfox.enable.context.menu.icons')};
                """, sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['nova'] != nova or meta['iconPref'] != icons):
                raise RuntimeError(f'unexpected Firefox process, profile, or preferences: {meta}')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox startup did not finish')
                time.sleep(.1)
            identity = m.execute_script("""
                const identity=ContextualIdentityService.getPublicIdentities()[0];
                if (!identity) throw new Error('no public container identity');
                const page='<a id="container-link" href="https://example.invalid/">container link fixture</a>';
                gBrowser.selectedTab=gBrowser.addTrustedTab('data:text/html,'+encodeURIComponent(page),
                  {userContextId:identity.userContextId});
                return identity.userContextId;
                """, sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 15
            while not m.execute_script("return location.href.startsWith('data:text/html') && !!document.getElementById('container-link')"):
                if time.monotonic() >= deadline:
                    raise RuntimeError('container link fixture did not load')
                time.sleep(.1)
            link = m.find_element('id', 'container-link')
            m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(link, button=2).perform()
            m.set_context('chrome')
            deadline = time.monotonic() + 15
            while True:
                state = m.execute_script("""
                    const popup=document.getElementById('contentAreaContextMenu');
                    const item=document.getElementById('context-openlinkincontainertab');
                    if (!popup || popup.state !== 'open' || !item || item.hidden) return null;
                    const r=item.getBoundingClientRect(), s=getComputedStyle(item);
                    if (r.width <= 0 || r.height <= 0) return null;
                    return {identity:gBrowser.selectedTab.userContextId,
                      principalIdentity:gBrowser.selectedBrowser.contentPrincipal.originAttributes.userContextId,
                      tag:item.localName, width:r.width,height:r.height, image:s.backgroundImage,
                      originalMatches:item.matches('context-openlinkincontainertab'),
                      idMatches:item.matches('#context-openlinkincontainertab'),
                      legacy:matchMedia('(-moz-bool-pref: "shyfox.enable.context.menu.icons")').matches,
                      modern:matchMedia('-moz-pref("shyfox.enable.context.menu.icons")').matches};
                    """, sandbox='system')
                if state:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'container context menu did not initialize: {state}')
                time.sleep(.1)
            if (state['identity'] != identity or state['principalIdentity'] != identity
                    or state['tag'] != 'menuitem' or state['originalMatches'] or not state['idMatches']
                    or state['modern'] != icons):
                raise RuntimeError(f'container context fixture failed: {state}')
            control = m.execute_script("""
                const item=document.getElementById('context-openlinkincontainertab');
                const prefix='@media -moz-pref("shyfox.enable.context.menu.icons") { ';
                const suffix=' { background-image: url("chrome://global/skin/icons/folder.svg") !important; } }';
                const sample=selector=>{
                  const uri='data:text/css,'+encodeURIComponent(prefix+selector+suffix);
                  window.windowUtils.loadSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET);
                  try { return getComputedStyle(item).backgroundImage; }
                  finally { window.windowUtils.removeSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET); }
                };
                return {original:sample('#contentAreaContextMenu context-openlinkincontainertab, #contentAreaContextMenu #context-openlinkinusercontext-menu'),
                  corrected:sample('#contentAreaContextMenu #context-openlinkincontainertab')};
                """, sandbox='system')
            expected = 'url("chrome://global/skin/icons/folder.svg")' if icons else 'none'
            if control['original'] != 'none' or control['corrected'] != expected:
                raise RuntimeError(f'container selector interventions were inconclusive: {control}')
            m.actions.sequence('key', 'keyboard').send_keys(Keys.ESCAPE).perform()
            deadline = time.monotonic() + 10
            while m.execute_script("return document.getElementById('contentAreaContextMenu').state", sandbox='system') != 'closed':
                if time.monotonic() >= deadline:
                    raise RuntimeError('container context menu did not close')
                time.sleep(.1)
            print(json.dumps({'firefox':meta, 'state':state, 'control':control}), flush=True)
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
    source = (ROOT / 'chrome/icons.css').read_text()
    if not re.search(r'(?m)^\s*context-openlinkincontainertab\s*,\s*#context-openlinkinusercontext-menu\s*\{', source):
        raise RuntimeError('container selector changed; update this diagnostic before rerunning')
    for nova in (False, True):
        for icons in (False, True):
            check(nova, icons)
