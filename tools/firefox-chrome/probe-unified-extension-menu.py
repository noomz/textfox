#!/usr/bin/env python3
"""Inspect a real unified-extensions menu with disposable synthetic add-ons."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import zipfile

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
PREF = 'shyfox.enable.context.menu.icons'
IDS = ('textfox-unified-a@example.invalid', 'textfox-unified-b@example.invalid')


def check(nova, enabled):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-unified-menu-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        archives = []
        for index, addon_id in enumerate(IDS):
            path = profile / f'fixture-{index}.xpi'
            manifest = {'manifest_version': 2, 'name': f'Textfox unified fixture {index}', 'version': '1.0',
                        'browser_specific_settings': {'gecko': {'id': addon_id}},
                        'browser_action': {'default_title': f'Textfox fixture {index}'}}
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('manifest.json', json.dumps(manifest))
            archives.append(str(path))
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova, PREF: enabled,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False}
        (profile / 'user.js').write_text(''.join(f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        env = {**os.environ, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'}
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access', '--new-instance',
                                    '--profile', str(profile), 'about:blank'], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            metadata = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,
                  build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  iconPref:Services.prefs.getBoolPref(arguments[0])};
                """, script_args=[PREF], sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova or metadata['iconPref'] != enabled):
                raise RuntimeError(f'unexpected Firefox process or profile: {metadata}')
            for addon_id, archive in zip(IDS, archives, strict=True):
                installed = m.execute_async_script("""
                    const done=arguments[arguments.length-1],file=Cc['@mozilla.org/file/local;1'].createInstance(Ci.nsIFile);
                    file.initWithPath(arguments[0]);AddonManager.installTemporaryAddon(file)
                      .then(a=>done({id:a.id,active:a.isActive}),e=>done({error:String(e)}));
                    """, script_args=[archive], sandbox='system')
                if installed.get('id') != addon_id or not installed.get('active'):
                    raise RuntimeError(f'fixture installation failed: {installed}')
            deadline = time.monotonic() + 20
            while True:
                button = m.execute_script("""
                    const e=document.getElementById('unified-extensions-button'),r=e?.getBoundingClientRect();
                    return !!e && r.width>0 && r.height>0;
                    """, sandbox='system')
                if button:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('unified extensions button unavailable')
                time.sleep(.1)
            m.find_element('id', 'unified-extensions-button').click()
            deadline = time.monotonic() + 20
            while True:
                items = m.execute_script("""
                    const view=document.getElementById('unified-extensions-view');
                    return arguments[0].map(id=>{
                      const item=view?.querySelector(`toolbaritem[data-extensionid="${id}"], unified-extensions-item[extension-id="${id}"]`);
                      const button=item?.querySelector('.unified-extensions-item-menu-button'),r=button?.getBoundingClientRect();
                      return {id,found:!!item,button:!!button,width:r?.width,height:r?.height};
                    });
                    """, script_args=[IDS], sandbox='system')
                if all(i['found'] and i['button'] and i['width'] > 0 and i['height'] > 0 for i in items):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'unified fixture buttons unavailable: {items}')
                time.sleep(.1)
            selector = f'toolbaritem[data-extensionid="{IDS[0]}"] .unified-extensions-item-menu-button, unified-extensions-item[extension-id="{IDS[0]}"] .unified-extensions-item-menu-button'
            m.find_element('css selector', selector).click()
            deadline = time.monotonic() + 15
            while True:
                menu = m.execute_script("""
                    const popup=document.getElementById('unified-extensions-context-menu');
                    if(popup?.state!=='open') return null;
                    const names=['manage-extension','remove-extension','report-extension','move-widget-up','move-widget-down'];
                    return {legacy:matchMedia('(-moz-bool-pref: "shyfox.enable.context.menu.icons")').matches,
                      modern:matchMedia('-moz-pref("shyfox.enable.context.menu.icons")').matches,
                      items:names.map(name=>{const e=popup.querySelector('.unified-extensions-context-menu-'+name);
                        if(!e)return {name,found:false};const s=getComputedStyle(e),r=e.getBoundingClientRect();
                        return {name,found:true,hidden:e.hidden,disabled:e.disabled,width:r.width,height:r.height,
                          image:s.backgroundImage,display:s.display,visibility:s.visibility};})};
                    """, sandbox='system')
                if menu:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('unified extension context menu did not open')
                time.sleep(.1)
            if menu['modern'] != enabled or not all(i['found'] for i in menu['items']):
                raise RuntimeError(f'unified menu selectors or preference unavailable: {menu}')
            control = m.execute_script("""
                const item=document.querySelector('#unified-extensions-context-menu .unified-extensions-context-menu-manage-extension');
                const css='@media -moz-pref("shyfox.enable.context.menu.icons") { #unified-extensions-context-menu .unified-extensions-context-menu-manage-extension { background-image: url("chrome://global/skin/icons/settings.svg") !important; } }';
                const uri='data:text/css,'+encodeURIComponent(css);
                window.windowUtils.loadSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET);
                try { return getComputedStyle(item).backgroundImage; }
                finally { window.windowUtils.removeSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET); }
                """, sandbox='system')
            expected='url("chrome://global/skin/icons/settings.svg")' if enabled else 'none'
            if control != expected:
                raise RuntimeError(f'unified menu icon control failed: {control}')
            order_script = """
                const {ExtensionCommon}=ChromeUtils.importESModule('resource://gre/modules/ExtensionCommon.sys.mjs');
                const ids=arguments[0].map(id=>ExtensionCommon.makeWidgetId(id)+'-browser-action');
                return CustomizableUI.getWidgetIdsInArea(CustomizableUI.AREA_ADDONS).filter(id=>ids.includes(id));
                """
            before=m.execute_script(order_script,script_args=[IDS],sandbox='system')
            if len(before)!=2 or menu['items'][4]['hidden'] or menu['items'][4]['disabled']:
                raise RuntimeError(f'move-down control unavailable: {before}, {menu}')
            m.find_element('css selector','#unified-extensions-context-menu .unified-extensions-context-menu-move-widget-down').click()
            deadline=time.monotonic()+15
            while True:
                after=m.execute_script(order_script,script_args=[IDS],sandbox='system')
                if after==list(reversed(before)):
                    break
                if time.monotonic()>=deadline:
                    raise RuntimeError(f'move-down did not reorder fixture widgets: {before}, {after}')
                time.sleep(.1)
            m.execute_script("document.getElementById('unified-extensions-context-menu').hidePopup()",sandbox='system')
            deadline=time.monotonic()+15
            while m.execute_script("return document.getElementById('unified-extensions-context-menu')?.state",sandbox='system')!='closed':
                if time.monotonic()>=deadline:
                    raise RuntimeError('unified context menu did not close after move')
                time.sleep(.1)
            m.find_element('css selector',selector).click()
            deadline=time.monotonic()+15
            while m.execute_script("return document.getElementById('unified-extensions-context-menu')?.state",sandbox='system')!='open':
                if time.monotonic()>=deadline:
                    raise RuntimeError('unified context menu did not reopen after move')
                time.sleep(.1)
            moved=m.execute_script("""
                const p=document.getElementById('unified-extensions-context-menu');
                return ['move-widget-up','move-widget-down'].map(name=>{const e=p.querySelector('.unified-extensions-context-menu-'+name);
                  const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {name,hidden:e.hidden,disabled:e.disabled,width:r.width,height:r.height,image:s.backgroundImage};});
                """,sandbox='system')
            if moved[0]['hidden'] or moved[0]['disabled'] or not moved[1]['hidden']:
                raise RuntimeError(f'moved menu state unexpected: {moved}')
            m.find_element('css selector','#unified-extensions-context-menu .unified-extensions-context-menu-move-widget-up').click()
            deadline=time.monotonic()+15
            while m.execute_script(order_script,script_args=[IDS],sandbox='system')!=before:
                if time.monotonic()>=deadline:
                    raise RuntimeError('move-up did not restore fixture order')
                time.sleep(.1)
            print(json.dumps({'firefox':metadata, 'fixtureButtons':items, 'menu':menu, 'control':control,
                              'move':{'before':before,'after':after,'movedMenu':moved,'restored':True}}), flush=True)
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
        for enabled in (False, True):
            check(nova, enabled)
