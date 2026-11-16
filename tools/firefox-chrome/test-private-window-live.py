#!/usr/bin/env python3
"""Check private-window URL bar results and configured borders in Firefox."""
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import tempfile
import time

from marionette_driver.keys import Keys

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(nova, icons):
    selector_path = os.environ.get('TEXTFOX_SELECTOR_INVENTORY')
    selector_records = None
    selector_query = None
    if selector_path:
        selector_records = [r for r in json.loads(Path(selector_path).read_text()) if r['file'] == 'chrome/icons.css']
        if not selector_records:
            raise ValueError('selector inventory has no icon records')
        selector_query = runpy.run_path(str(ROOT / 'tools/firefox-chrome/probe-selector-dom.py'))['query']
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-private-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(
            ':root { --tf-accent: rgb(19, 83, 137); --tf-border-width: 3px; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'shyfox.enable.context.menu.icons': icons,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile),
                                    '--private-window', 'about:privatebrowsing'], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled'),private:PrivateBrowsingUtils.isWindowPrivate(window),iconPref:Services.prefs.getBoolPref('shyfox.enable.context.menu.icons')}", sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['nova'] != nova or not meta['private'] or meta['iconPref'] != icons):
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
            if m.execute_script('return gBrowser.selectedBrowser.currentURI.spec', sandbox='system') == 'about:blank':
                raise RuntimeError('navigation fixture already at destination')
            m.actions.sequence('key', 'keyboard').send_keys(Keys.ESCAPE).perform()
            m.actions.sequence('key', 'keyboard').key_down(Keys.CONTROL).key_down('l').key_up('l').key_up(Keys.CONTROL).perform()
            field = m.find_element('id', 'urlbar-input')
            m.execute_script('gURLBar.select()', sandbox='system')
            field.send_keys('about:blank')
            entered = m.execute_script('return gURLBar.value', sandbox='system')
            if entered != 'about:blank':
                raise RuntimeError(f'private URL bar input did not match destination: {entered}')
            field.send_keys(Keys.ENTER)
            deadline = time.monotonic() + 15
            while not m.execute_script("return gBrowser.selectedBrowser.currentURI.spec === 'about:blank'", sandbox='system'):
                if time.monotonic() >= deadline:
                    state = m.execute_script("return {uri:gBrowser.selectedBrowser.currentURI.spec, value:gURLBar.value}", sandbox='system')
                    raise RuntimeError(f'private URL bar navigation did not complete: {state}')
                time.sleep(.1)
            if not m.execute_script('return PrivateBrowsingUtils.isWindowPrivate(window)', sandbox='system'):
                raise RuntimeError('navigation left private window')
            print(json.dumps({'nova':nova, 'privateNavigation':'about:blank'}), flush=True)
            m.set_context('content')
            m.navigate('data:text/html,<a href="https://example.invalid/">private fixture link</a>')
            m.actions.sequence('pointer','mouse',{'pointerType':'mouse'}).click(m.find_element('css selector','a'),button=2).perform()
            m.set_context('chrome')
            deadline=time.monotonic()+15
            while m.execute_script("return document.getElementById('contentAreaContextMenu')?.state",sandbox='system')!='open':
                if time.monotonic()>=deadline:
                    raise RuntimeError('private link context menu did not open')
                time.sleep(.1)
            private_menu=m.execute_script("""
                const popup=document.getElementById('contentAreaContextMenu');
                return {private:PrivateBrowsingUtils.isWindowPrivate(window),
                  legacy:matchMedia('(-moz-bool-pref: "shyfox.enable.context.menu.icons")').matches,
                  modern:matchMedia('-moz-pref("shyfox.enable.context.menu.icons")').matches,
                  items:['context-openlinkintab','context-openlinkprivate'].map(id=>{const e=document.getElementById(id);
                    if(!e)return {id,found:false};const s=getComputedStyle(e),r=e.getBoundingClientRect();
                    return {id,found:true,inPopup:popup.contains(e),hidden:e.hidden,display:s.display,visibility:s.visibility,
                      width:r.width,height:r.height,image:s.backgroundImage};})};
                """,sandbox='system')
            if (not private_menu['private'] or private_menu['modern']!=icons
                    or not all(i['found'] and i['inPopup'] and not i['hidden'] and i['display']!='none'
                               and i['visibility']=='visible' and i['width']>0 and i['height']>0 for i in private_menu['items'])):
                raise RuntimeError(f'private link context fixture unavailable: {private_menu}')
            control=m.execute_script("""
                const css='@media -moz-pref("shyfox.enable.context.menu.icons") { #contentAreaContextMenu #context-openlinkprivate { background-image: url("chrome://browser/skin/privateBrowsing.svg") !important; } }';
                const uri='data:text/css,'+encodeURIComponent(css);
                window.windowUtils.loadSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET);
                try { return getComputedStyle(document.getElementById('context-openlinkprivate')).backgroundImage; }
                finally { window.windowUtils.removeSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET); }
                """,sandbox='system')
            expected='url("chrome://browser/skin/privateBrowsing.svg")' if icons else 'none'
            if control!=expected:
                raise RuntimeError(f'private context icon control failed: {control}')
            selectors=selector_query(m,selector_records) if selector_query else []
            m.actions.sequence('key','keyboard').send_keys(Keys.ESCAPE).perform()
            print(json.dumps({'firefox':meta,'privateMenu':private_menu,'modernControl':control,'selectorRecords':selectors}),flush=True)
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
        for icons in (False, True):
            check(nova, icons)
