#!/usr/bin/env python3
"""Inspect a completed local download's context menu in disposable profiles."""
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
    with tempfile.TemporaryDirectory(prefix='tf-download-icons-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port':port, 'browser.nova.enabled':nova,
                 'shyfox.enable.context.menu.icons':icons,
                 'browser.download.alwaysOpenPanel':False,
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
            target = profile / 'download-fixture.txt'
            completed = m.execute_async_script("""
                const done=arguments[arguments.length-1], target=arguments[0];
                (async()=>{
                  const {Downloads}=ChromeUtils.importESModule('resource://gre/modules/Downloads.sys.mjs');
                  const list=await Downloads.getList(Downloads.PUBLIC);
                  const download=await Downloads.createDownload({source:'data:text/plain,textfox-download-fixture',target});
                  await list.add(download);
                  await download.start();
                  done({succeeded:download.succeeded, path:download.target.path});
                })().catch(e=>done({error:String(e)}));
                """, script_args=[str(target)], sandbox='system')
            if completed.get('error') or not completed.get('succeeded') or completed.get('path') != str(target):
                raise RuntimeError(f'local download did not complete: {completed}')
            if target.read_text() != 'textfox-download-fixture':
                raise RuntimeError('local download content did not match fixture')
            m.find_element('id', 'downloads-button').click()
            deadline = time.monotonic() + 20
            while not m.execute_script("return document.getElementById('downloadsPanel')?.state === 'open' && !!document.querySelector('#downloadsListBox richlistitem')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('downloads panel did not open with fixture')
                time.sleep(.1)
            item = m.find_element('css selector', '#downloadsListBox richlistitem')
            m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(item, button=2).perform()
            deadline = time.monotonic() + 15
            while True:
                menu = m.execute_script("""
                    const popup=document.getElementById('downloadsContextMenu');
                    if (!popup || popup.state !== 'open') return null;
                    return {legacy:matchMedia('(-moz-bool-pref: "shyfox.enable.context.menu.icons")').matches,
                      modern:matchMedia('-moz-pref("shyfox.enable.context.menu.icons")').matches,
                      items:[...popup.querySelectorAll('menuitem')].map(e=>{
                        const s=getComputedStyle(e), r=e.getBoundingClientRect();
                        return {className:e.getAttribute('class'), command:e.getAttribute('command'),
                          hidden:e.hidden, disabled:e.disabled, display:s.display, visibility:s.visibility,
                          width:r.width,height:r.height,image:s.backgroundImage};
                      })};
                    """, sandbox='system')
                if menu and any(not x['hidden'] and x['width'] > 0 and x['height'] > 0 for x in menu['items']):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'download context menu did not open: {menu}')
                time.sleep(.1)
            visible = [x for x in menu['items'] if not x['hidden'] and x['display'] != 'none'
                       and x['visibility'] == 'visible' and x['width'] > 0 and x['height'] > 0]
            if menu['modern'] != icons or not any(x['command'] == 'downloadsCmd_deleteFile' for x in visible):
                raise RuntimeError(f'download context fixture or preference control failed: {menu}')
            intervention = m.execute_script("""
                const css='@media -moz-pref("shyfox.enable.context.menu.icons") { #downloadsContextMenu .downloadShowMenuItem { background-image: url("chrome://global/skin/icons/folder.svg") !important; } }';
                const uri='data:text/css,'+encodeURIComponent(css);
                window.windowUtils.loadSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET);
                try { return getComputedStyle(document.querySelector('#downloadsContextMenu .downloadShowMenuItem')).backgroundImage; }
                finally { window.windowUtils.removeSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET); }
                """, sandbox='system')
            expected = 'url("chrome://global/skin/icons/folder.svg")' if icons else 'none'
            if intervention != expected:
                raise RuntimeError(f'download icon intervention failed: {intervention}')
            selector_results = selector_query(m, selector_records) if selector_query else []
            if selector_query:
                candidates = [r for r in selector_results if '#downloadsContextMenu' in r['expandedSelector']]
                if not candidates or not any(r['status'] == 'structural-match' for r in candidates):
                    raise RuntimeError(f'download selector inventory did not match: {candidates}')
            remove = m.find_element('css selector', '#downloadsContextMenu .downloadRemoveFromHistoryMenuItem')
            m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(remove).perform()
            deadline = time.monotonic() + 10
            while m.execute_script("return document.getElementById('downloadsContextMenu').state", sandbox='system') != 'closed':
                if time.monotonic() >= deadline:
                    raise RuntimeError('download context menu did not close')
                time.sleep(.1)
            deadline = time.monotonic() + 10
            while True:
                remaining = m.execute_async_script("""
                    const done=arguments[arguments.length-1];
                    const {Downloads}=ChromeUtils.importESModule('resource://gre/modules/Downloads.sys.mjs');
                    Downloads.getList(Downloads.PUBLIC).then(list=>list.getAll())
                      .then(items=>done(items.map(item=>item.target.path)),error=>done({error:String(error)}));
                    """, sandbox='system')
                if isinstance(remaining, list) and str(target) not in remaining:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'download was not removed from history: {remaining}')
                time.sleep(.1)
            if target.read_text() != 'textfox-download-fixture':
                raise RuntimeError('removing download history altered fixture file')
            print(json.dumps({'firefox':meta, 'download':completed, 'menu':menu,
                              'modernIntervention':intervention,
                              'historyAfter':remaining, 'fixtureFileIntact':True,
                              'selectorRecords':selector_results}), flush=True)
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
