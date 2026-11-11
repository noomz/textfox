#!/usr/bin/env python3
"""Inspect built-in bookmarks menu icon targets in disposable Firefox profiles."""
import json
import os
from pathlib import Path
import re
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
    with tempfile.TemporaryDirectory(prefix='tf-bookmarks-menu-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port':port,'browser.nova.enabled':nova,
                 'shyfox.enable.context.menu.icons':icons,
                 'toolkit.legacyUserProfileCustomizations.stylesheets':True,
                 'browser.shell.checkDefaultBrowser':False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k,v in prefs.items()))
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox','--marionette','-remote-allow-system-access',
                                    '--new-instance','--profile',str(profile),'about:blank'],
                                   env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
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
            m.execute_script("CustomizableUI.addWidgetToArea('bookmarks-menu-button',CustomizableUI.AREA_NAVBAR)", sandbox='system')
            deadline = time.monotonic() + 15
            while not m.execute_script("const e=document.getElementById('bookmarks-menu-button'), r=e?.getBoundingClientRect(); return !!r && r.width > 0 && r.height > 0", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('bookmarks menu button unavailable')
                time.sleep(.1)
            states = {}
            for visible in (False, True):
                m.execute_script("setToolbarVisibility(document.getElementById('PersonalToolbar'),arguments[0])", script_args=[visible], sandbox='system')
                deadline = time.monotonic() + 10
                while m.execute_script("return document.getElementById('PersonalToolbar').collapsed", sandbox='system') == visible:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('bookmarks toolbar visibility did not change')
                    time.sleep(.1)
                m.find_element('id','bookmarks-menu-button').click()
                deadline = time.monotonic() + 15
                while not m.execute_script("return document.getElementById('BMB_bookmarksPopup')?.state === 'open'", sandbox='system'):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('bookmarks menu did not open')
                    time.sleep(.1)
                m.actions.sequence('pointer','mouse',{'pointerType':'mouse'}).pointer_move(
                    0,0,origin=m.find_element('id','BMB_searchBookmarks')).perform()
                m.actions.sequence('pointer','mouse',{'pointerType':'mouse'}).pointer_move(
                    0,0,origin=m.find_element('id','BMB_bookmarksToolbar')).perform()
                deadline = time.monotonic() + 15
                while not m.execute_script("return document.getElementById('BMB_bookmarksToolbarPopup')?.state === 'open'", sandbox='system'):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('bookmarks toolbar submenu did not open')
                    time.sleep(.1)
                state = m.execute_script("""
                    const popup=document.getElementById('BMB_bookmarksPopup'), toolbar=document.getElementById('PersonalToolbar');
                    const ts=getComputedStyle(toolbar), tr=toolbar.getBoundingClientRect();
                    const ids=['BMB_viewBookmarksToolbar','BMB_viewBookmarksSidebar','BMB_searchBookmarks',
                      'BMB_mobileBookmarks','BMB_bookmarksShowAllTop','BMB_bookmarksShowAll'];
                    return {toolbarCollapsed:toolbar.collapsed,toolbarDisplay:ts.display,
                      toolbarWidth:tr.width,toolbarHeight:tr.height,
                      toolbarCollapsedAttribute:toolbar.getAttribute('collapsed'),
                      rootId:document.documentElement.id,
                      legacy:matchMedia('(-moz-bool-pref: "shyfox.enable.context.menu.icons")').matches,
                      modern:matchMedia('-moz-pref("shyfox.enable.context.menu.icons")').matches,
                      items:Object.fromEntries(ids.map(id=>{const e=document.getElementById(id); if(!e)return [id,null];
                        const s=getComputedStyle(e),r=e.getBoundingClientRect();
                        return [id,{hidden:e.hidden,display:s.display,visibility:s.visibility,width:r.width,height:r.height,
                          image:s.backgroundImage,inPopup:popup.contains(e),
                          hiddenToolbarRule:e.matches('#main-window:has(#PersonalToolbar[collapsed]) #BMB_bookmarksPopup #BMB_viewBookmarksToolbar')}];
                      }))};
                    """, sandbox='system')
                toggle = state['items']['BMB_viewBookmarksToolbar']
                if (state['toolbarCollapsed'] == visible or state['modern'] != icons or not toggle
                        or not toggle['inPopup']
                        or toggle['width'] <= 0 or toggle['height'] <= 0):
                    raise RuntimeError(f'bookmarks menu fixture or conditional selector failed: {state}')
                selector_states = {}
                if selector_query:
                    selector_states['popup'] = selector_query(m, selector_records)
                    if not visible:
                        original = m.execute_script("const e=document.getElementById('PersonalToolbar'),v=e.getAttribute('collapsed'); e.setAttribute('collapsed',''); return v", sandbox='system')
                        try:
                            selector_states['collapsedControl'] = selector_query(m, selector_records)
                        finally:
                            m.execute_script("const e=document.getElementById('PersonalToolbar'); if(arguments[0]===null)e.removeAttribute('collapsed'); else e.setAttribute('collapsed',arguments[0])", script_args=[original], sandbox='system')
                collapsed_control = None
                if not visible:
                    collapsed_control = m.execute_script("""
                        const bar=document.getElementById('PersonalToolbar'), item=document.getElementById('BMB_viewBookmarksToolbar');
                        const original=bar.getAttribute('collapsed');
                        bar.setAttribute('collapsed','');
                        try { return {matches:item.matches('#main-window:has(#PersonalToolbar[collapsed]) #BMB_bookmarksPopup #BMB_viewBookmarksToolbar'),
                          property:bar.collapsed,attribute:bar.getAttribute('collapsed')}; }
                        finally { if(original === null) bar.removeAttribute('collapsed'); else bar.setAttribute('collapsed',original); }
                        """, sandbox='system')
                    restored = m.execute_script("const e=document.getElementById('PersonalToolbar'); return {property:e.collapsed,attribute:e.getAttribute('collapsed')}", sandbox='system')
                    collapsed_control['restored'] = restored
                    if (not collapsed_control['matches'] or not collapsed_control['property']
                            or not restored['property'] or restored['attribute'] != state['toolbarCollapsedAttribute']):
                        raise RuntimeError(f'collapsed attribute selector intervention failed: {collapsed_control}')
                control = m.execute_script("""
                    const item=document.getElementById('BMB_viewBookmarksToolbar');
                    const css='@media -moz-pref("shyfox.enable.context.menu.icons") { #BMB_bookmarksPopup #BMB_viewBookmarksToolbar { background-image: url("chrome://global/skin/icons/folder.svg") !important; } }';
                    const uri='data:text/css,'+encodeURIComponent(css);
                    window.windowUtils.loadSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET);
                    try { return getComputedStyle(item).backgroundImage; }
                    finally { window.windowUtils.removeSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET); }
                    """, sandbox='system')
                if icons and not visible:
                    # The production collapsed-toolbar rule (three IDs, !important) outranks
                    # the two-ID temporary control, so the sampled value is the production icon.
                    if 'eye-show.svg' not in control:
                        raise RuntimeError(f'production collapsed-toolbar eye icon missing: {control}')
                else:
                    expected = 'url("chrome://global/skin/icons/folder.svg")' if icons else 'none'
                    if control != expected:
                        raise RuntimeError(f'bookmarks menu icon intervention failed: {control}')
                toggled = None
                if not visible:
                    m.find_element('id','BMB_viewBookmarksToolbar').click()
                    deadline = time.monotonic() + 10
                    while m.execute_script("return document.getElementById('PersonalToolbar').collapsed", sandbox='system'):
                        if time.monotonic() >= deadline:
                            raise RuntimeError('bookmarks submenu did not show toolbar')
                        time.sleep(.1)
                    toggled = True
                m.actions.sequence('key','keyboard').send_keys(Keys.ESCAPE).perform()
                deadline = time.monotonic() + 10
                while m.execute_script("return document.getElementById('BMB_bookmarksToolbarPopup').state", sandbox='system') != 'closed':
                    if time.monotonic() >= deadline:
                        raise RuntimeError('bookmarks toolbar submenu did not close')
                    time.sleep(.1)
                m.actions.sequence('key','keyboard').send_keys(Keys.ESCAPE).perform()
                deadline = time.monotonic() + 10
                while m.execute_script("return document.getElementById('BMB_bookmarksPopup').state", sandbox='system') != 'closed':
                    if time.monotonic() >= deadline:
                        open_states=m.execute_script("return {root:document.getElementById('BMB_bookmarksPopup').state,submenu:document.getElementById('BMB_bookmarksToolbarPopup').state,collapsed:document.getElementById('PersonalToolbar').collapsed}", sandbox='system')
                        raise RuntimeError(f'bookmarks popup did not close: {open_states}')
                    time.sleep(.1)
                states['visible' if visible else 'hidden'] = {'menu':state,'modernControl':control,
                                                               'collapsedControl':collapsed_control,'showAction':toggled,
                                                               'selectorStates':selector_states}
            print(json.dumps({'firefox':meta,'toolbarStates':states}),flush=True)
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
    selector = r'#main-window:has\(#PersonalToolbar\[collapsed\]\)\s+#BMB_bookmarksPopup\s+#BMB_viewBookmarksToolbar'
    if not re.search(selector, source):
        raise RuntimeError('collapsed-toolbar selector changed; update this diagnostic before rerunning')
    for nova in (False, True):
        for icons in (False, True):
            check(nova, icons)
