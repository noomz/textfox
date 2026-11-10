#!/usr/bin/env python3
"""Inspect classic sidebar-action icon targets using disposable synthetic add-ons."""
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import tempfile
import time
import zipfile

from marionette_driver.keys import Keys
from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
PREF = 'shyfox.enable.ext.mono.context.icons'
IDS = ('{446900e4-71c2-419f-a6a7-df9c091e268b}', '{b9db16a4-6edc-47ec-a1f4-b86292ed211d}')


def check(nova, enabled):
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
    with tempfile.TemporaryDirectory(prefix='tf-sidebar-extension-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        archives = []
        for index, addon_id in enumerate(IDS):
            path = profile / f'fixture-{index}.xpi'
            manifest = {'manifest_version': 2, 'name': f'Textfox sidebar fixture {index}', 'version': '1.0',
                        'browser_specific_settings': {'gecko': {'id': addon_id}},
                        'icons': {'16': 'fixture.svg'},
                        'sidebar_action': {'default_title': f'Textfox sidebar {index}',
                                           'default_panel': 'sidebar.html', 'default_icon': 'fixture.svg'}}
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('manifest.json', json.dumps(manifest))
                archive.writestr('sidebar.html', '<!doctype html><title>Textfox sidebar fixture</title>')
                archive.writestr('fixture.svg', '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><path fill="black" d="M0 0h16v16H0z"/></svg>')
            archives.append(path)
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova, 'sidebar.revamp': False, PREF: enabled,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True, 'browser.shell.checkDefaultBrowser': False}
        (profile / 'user.js').write_text(''.join(f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access', '--new-instance',
                                    '--profile', str(profile), 'about:blank'],
                                   env={**os.environ, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,
                  build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  revamp:Services.prefs.getBoolPref('sidebar.revamp'),iconPref:Services.prefs.getBoolPref(arguments[0])};
                """, script_args=[PREF], sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['nova'] != nova or meta['revamp'] or meta['iconPref'] != enabled):
                raise RuntimeError(f'unexpected Firefox process or profile: {meta}')
            for addon_id, path in zip(IDS, archives, strict=True):
                installed = m.execute_async_script("""
                    const done=arguments[arguments.length-1],file=Cc['@mozilla.org/file/local;1'].createInstance(Ci.nsIFile);
                    file.initWithPath(arguments[0]);AddonManager.installTemporaryAddon(file)
                      .then(a=>done({id:a.id,active:a.isActive}),e=>done({error:String(e)}));
                    """, script_args=[str(path)], sandbox='system')
                if installed.get('id') != addon_id or not installed.get('active'):
                    raise RuntimeError(f'sidebar fixture installation failed: {installed}')
            ids = m.execute_script("""
                const {ExtensionCommon}=ChromeUtils.importESModule('resource://gre/modules/ExtensionCommon.sys.mjs');
                return arguments[0].map(id=>'sidebarswitcher_menu_'+ExtensionCommon.makeWidgetId(id)+'-sidebar-action');
                """, script_args=[IDS], sandbox='system')
            m.execute_script('SidebarController.show("viewBookmarksSidebar")', sandbox='system')
            deadline = time.monotonic() + 20
            while not m.execute_script("return !document.getElementById('sidebar-box').hidden && arguments[0].every(id=>!!document.getElementById(id))", script_args=[ids], sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'sidebar switcher fixture unavailable: {ids}')
                time.sleep(.1)
            header = m.execute_script("const e=document.getElementById('sidebar-header'),r=e.getBoundingClientRect();return {display:getComputedStyle(e).display,width:r.width,height:r.height}", sandbox='system')
            saved = m.execute_script("const e=document.getElementById('sidebar-header');const value=e.style.getPropertyValue('display'),priority=e.style.getPropertyPriority('display');e.style.setProperty('display','flex','important');return {value,priority}", sandbox='system')
            try:
                m.find_element('id', 'sidebar-switcher-target').click()
                deadline = time.monotonic() + 15
                while m.execute_script("return document.getElementById('sidebarMenu-popup')?.state", sandbox='system') != 'open':
                    if time.monotonic() >= deadline:
                        raise RuntimeError('classic sidebar menu did not open')
                    time.sleep(.1)
                menu = m.execute_script("""
                    return arguments[0].map(id=>{const e=document.getElementById(id),s=getComputedStyle(e),r=e.getBoundingClientRect();
                      return {id,hidden:e.hidden,display:s.display,visibility:s.visibility,width:r.width,height:r.height,image:s.backgroundImage};});
                    """, script_args=[ids], sandbox='system')
                if any(i['hidden'] or i['display']=='none' or i['visibility']!='visible' or i['width']<=0 or i['height']<=0 for i in menu):
                    raise RuntimeError(f'sidebar extension items not visible: {menu}')
                media=m.execute_script("return {legacy:matchMedia('(-moz-bool-pref: \\\"shyfox.enable.ext.mono.context.icons\\\")').matches,modern:matchMedia('-moz-pref(\\\"shyfox.enable.ext.mono.context.icons\\\")').matches}", sandbox='system')
                if media['modern']!=enabled or 'video-download-helper.svg' not in menu[1]['image']:
                    raise RuntimeError(f'sidebar preference or unconditional icon failed: {media}, {menu}')
                selectors = {'menu': selector_query(m, selector_records)} if selector_query else {}
                control = m.execute_script("""
                    const id=arguments[0],url=arguments[1];
                    const css=`@media -moz-pref("shyfox.enable.ext.mono.context.icons") { #${id} { background-image:url("${url}") !important; } }`;
                    const uri='data:text/css,'+encodeURIComponent(css);
                    window.windowUtils.loadSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET);
                    try{return getComputedStyle(document.getElementById(id)).backgroundImage;}
                    finally{window.windowUtils.removeSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET);}
                    """, script_args=[ids[0], (profile / 'chrome/icons/bitwarden.svg').as_uri()], sandbox='system')
                if ('bitwarden.svg' in control) != enabled:
                    raise RuntimeError(f'sidebar modern-query control failed: {control}')
                m.actions.sequence('key','keyboard').send_keys(Keys.ESCAPE).perform()
                deadline=time.monotonic()+10
                while m.execute_script("return document.getElementById('sidebarMenu-popup').state",sandbox='system')!='closed':
                    if time.monotonic()>=deadline:
                        raise RuntimeError('sidebar switcher popup did not close')
                    time.sleep(.1)
                actions=[]
                for item_id in ids:
                    command=item_id.removeprefix('sidebarswitcher_menu_')
                    shown=m.execute_async_script("""
                        const done=arguments[arguments.length-1];
                        SidebarController.show(arguments[0]).then(()=>done(true),error=>done(String(error)));
                        """,script_args=[command],sandbox='system')
                    if shown is not True:
                        raise RuntimeError(f'sidebar action failed to open: {shown}')
                    current=m.execute_script("""
                        const box=document.getElementById('sidebar-box'),target=document.getElementById('sidebar-switcher-target'),icon=document.getElementById('sidebar-icon');
                        const s=getComputedStyle(icon),r=icon.getBoundingClientRect();return {command:box.getAttribute('sidebarcommand'),style:target.getAttribute('style'),
                          width:r.width,height:r.height,display:s.display,image:s.listStyleImage};
                        """,sandbox='system')
                    if current['command']!=command:
                        raise RuntimeError(f'wrong sidebar action selected: {current}')
                    actions.append(current)
                    if selector_query:
                        selectors[f'action{len(actions)}'] = selector_query(m, selector_records)
            finally:
                m.execute_script("const s=document.getElementById('sidebar-header').style; if(arguments[0].value)s.setProperty('display',arguments[0].value,arguments[0].priority);else s.removeProperty('display')", script_args=[saved], sandbox='system')
            print(json.dumps({'firefox':meta,'header':header,'headerIntervention':True,'items':menu,
                              'media':media,'control':control,'actions':actions,'selectorStates':selectors}), flush=True)
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
