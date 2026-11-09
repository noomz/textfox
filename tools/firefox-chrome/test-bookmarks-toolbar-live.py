#!/usr/bin/env python3
"""Inspect bookmarks toolbar alignment in disposable Firefox profiles."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from marionette_driver.keys import Keys

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def inspect_places(m, item, icons, expected_id, activate=False):
    m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(item, button=2).perform()
    deadline = time.monotonic() + 15
    while True:
        menu = m.execute_script("""
            const popup=document.getElementById('placesContext');
            if (!popup || popup.state !== 'open') return null;
            return {legacy:matchMedia('(-moz-bool-pref: "shyfox.enable.context.menu.icons")').matches,
              modern:matchMedia('-moz-pref("shyfox.enable.context.menu.icons")').matches,
              items:[...popup.querySelectorAll('menuitem[id],menu[id]')].map(e=>{
                const s=getComputedStyle(e), r=e.getBoundingClientRect();
                return {id:e.id,hidden:e.hidden,disabled:e.disabled,display:s.display,
                  visibility:s.visibility,width:r.width,height:r.height,image:s.backgroundImage};
              })};
            """, sandbox='system')
        if menu and any(not x['hidden'] and x['width'] > 0 and x['height'] > 0 for x in menu['items']):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Places context menu did not open: {menu}')
        time.sleep(.1)
    visible = {x['id'] for x in menu['items'] if not x['hidden'] and x['display'] != 'none'
               and x['visibility'] == 'visible' and x['width'] > 0 and x['height'] > 0}
    if menu['modern'] != icons or expected_id not in visible:
        raise RuntimeError(f'Places context fixture or preference control failed: {menu}')
    if activate:
        target = m.find_element('id', expected_id)
        m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(target).perform()
    else:
        m.actions.sequence('key', 'keyboard').send_keys(Keys.ESCAPE).perform()
    deadline = time.monotonic() + 10
    while m.execute_script("return document.getElementById('placesContext').state", sandbox='system') != 'closed':
        if time.monotonic() >= deadline:
            raise RuntimeError('Places context menu did not close')
        time.sleep(.1)
    return menu


def check(nova, alignment, icons):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-bookmarks-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(
            f':root {{ --tf-bookmarks-alignment: {alignment}; }}\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'shyfox.enable.context.menu.icons': icons,
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
                  nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  iconPref:Services.prefs.getBoolPref('shyfox.enable.context.menu.icons')};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova or metadata['iconPref'] != icons):
                raise RuntimeError('unexpected Firefox process, profile, or Nova preference')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox startup did not finish')
                time.sleep(.1)
            inserted = m.execute_async_script("""
                const done = arguments[arguments.length - 1];
                setToolbarVisibility(document.getElementById('PersonalToolbar'), true);
                PlacesUtils.bookmarks.insert({parentGuid:PlacesUtils.bookmarks.toolbarGuid,
                  url:'about:robots', title:'Textfox bookmark fixture'})
                  .then(() => done(true), error => done(String(error)));
                """, sandbox='system')
            if inserted is not True:
                raise RuntimeError(f'could not insert bookmark: {inserted}')
            deadline = time.monotonic() + 20
            while not m.execute_script("return !!document.querySelector('#PlacesToolbarItems .bookmark-item[label=\"Textfox bookmark fixture\"]')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('bookmark toolbar item did not appear')
                time.sleep(.1)
            result = m.execute_script("""
                const toolbar = document.getElementById('PersonalToolbar');
                const container = document.getElementById('PlacesToolbarItems');
                const item = container.querySelector('.bookmark-item[label="Textfox bookmark fixture"]');
                const c = container.getBoundingClientRect(), i = item.getBoundingClientRect();
                return {option:getComputedStyle(document.documentElement).getPropertyValue('--tf-bookmarks-alignment').trim(),
                  display:getComputedStyle(container).display, alignment:getComputedStyle(container).justifyContent,
                  toolbarVisible:!toolbar.collapsed && getComputedStyle(toolbar).display !== 'none',
                  container:{left:c.left,right:c.right,width:c.width},
                  item:{left:i.left,right:i.right,width:i.width,height:i.height}};
                """, sandbox='system')
            print(json.dumps({'firefox': metadata, 'requested': alignment, 'result': result}), flush=True)
            if (result['option'] != alignment or result['alignment'] != alignment
                    or result['display'] != 'flex' or not result['toolbarVisible']
                    or result['item']['width'] <= 0 or result['item']['height'] <= 0):
                raise RuntimeError(f'bookmark toolbar configuration not applied: {result}')
            container, item = result['container'], result['item']
            if alignment == 'center':
                offset = abs((item['left'] + item['right']) - (container['left'] + container['right'])) / 2
            else:
                offset = abs(item['left'] - container['left'])
            if offset > 4:
                raise RuntimeError(f'bookmark is not positioned at requested alignment: {result}')
            bookmark = m.find_element('css selector', '#PlacesToolbarItems .bookmark-item[label="Textfox bookmark fixture"]')
            menu = inspect_places(m, bookmark, icons, 'placesContext_show_bookmark:info')
            print(json.dumps({'firefox':metadata, 'placesKind':'bookmark', 'placesContext':menu}), flush=True)
            bookmark.click()
            deadline = time.monotonic() + 15
            while m.execute_script('return gBrowser.currentURI.spec', sandbox='system') != 'about:robots':
                if time.monotonic() >= deadline:
                    raise RuntimeError('bookmark did not navigate to its local target')
                time.sleep(.1)
            inserted_folder = m.execute_async_script("""
                const done=arguments[arguments.length-1];
                PlacesUtils.bookmarks.insert({parentGuid:PlacesUtils.bookmarks.toolbarGuid,
                  type:PlacesUtils.bookmarks.TYPE_FOLDER, title:'Textfox folder fixture'})
                  .then(folder=>PlacesUtils.bookmarks.insert({parentGuid:folder.guid,
                    url:'about:mozilla', title:'Textfox folder child'}))
                  .then(()=>done(true), error=>done(String(error)));
                """, sandbox='system')
            if inserted_folder is not True:
                raise RuntimeError(f'could not insert bookmark folder: {inserted_folder}')
            folder_selector = '#PlacesToolbarItems .bookmark-item[label="Textfox folder fixture"]'
            deadline = time.monotonic() + 20
            while not m.execute_script('return !!document.querySelector(arguments[0])', script_args=[folder_selector], sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('bookmark folder did not appear')
                time.sleep(.1)
            folder = m.find_element('css selector', folder_selector)
            count_before = m.execute_script('return gBrowser.tabs.length', sandbox='system')
            folder_menu = inspect_places(m, folder, icons, 'placesContext_openBookmarkContainer:tabs', activate=True)
            deadline = time.monotonic() + 15
            while True:
                opened = m.execute_script("return {count:gBrowser.tabs.length, urls:[...gBrowser.tabs].map(t=>t.linkedBrowser.currentURI.spec)}", sandbox='system')
                if opened['count'] == count_before + 1 and 'about:mozilla' in opened['urls']:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'folder context command did not open fixture child: {opened}')
                time.sleep(.1)
            print(json.dumps({'firefox':metadata, 'placesKind':'folder', 'placesContext':folder_menu,
                              'openedTabs':opened}), flush=True)
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
        for alignment in ('center', 'left'):
            for icons in (False, True):
                check(nova, alignment, icons)
