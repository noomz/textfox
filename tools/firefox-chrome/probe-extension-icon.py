#!/usr/bin/env python3
"""Inspect extension toolbar and context icon rules in disposable Firefox profiles.

The temporary extension uses an existing selector's ID only inside the fixture.
"""
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
EXTENSION_ID = 'uBlock0@raymondhill.net'
PREFERENCE = 'shyfox.enable.ext.mono.toolbar.icons'
CONTEXT_PREFERENCE = 'shyfox.enable.ext.mono.context.icons'
MENU_PREFERENCE = 'shyfox.enable.context.menu.icons'


def check(nova, enabled, context_enabled, menu_enabled):
    selector_path=os.environ.get('TEXTFOX_SELECTOR_INVENTORY')
    selector_records=None
    selector_query=None
    if selector_path:
        selector_records=[r for r in json.loads(Path(selector_path).read_text()) if r['file']=='chrome/icons.css'
                          and r['selector']=='#toggle_PersonalToolbar' and '#toolbar-context-menu' in r['expandedSelector']]
        if len(selector_records)!=1:
            raise ValueError('selector inventory has no unique toolbar bookmark-toggle record')
        selector_query=runpy.run_path(str(ROOT / 'tools/firefox-chrome/probe-selector-dom.py'))['query']
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-extension-icon-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        addon = profile / 'textfox-fixture.xpi'
        manifest = {'manifest_version':2, 'name':'Textfox selector fixture', 'version':'1.0',
                    'browser_specific_settings':{'gecko':{'id':EXTENSION_ID}},
                    'browser_action':{'default_title':'Textfox fixture', 'default_icon':'fixture.svg'},
                    'permissions':['menus'], 'background':{'scripts':['background.js']}}
        with zipfile.ZipFile(addon, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            archive.writestr('fixture.svg', '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><path fill="black" d="M0 0h16v16H0z"/></svg>')
            archive.writestr('background.js', 'browser.menus.create({id:"uBlock0-fixture",title:"Textfox context fixture",contexts:["link"]}); browser.browserAction.setBadgeText({text:"7"});')
        prefs = {'marionette.port':port, 'browser.nova.enabled':nova, PREFERENCE:enabled, CONTEXT_PREFERENCE:context_enabled,
                 MENU_PREFERENCE:menu_enabled,
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
            metadata = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,
                  version:Services.appinfo.version,build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  iconPref:Services.prefs.getBoolPref(arguments[0]),
                  contextPref:Services.prefs.getBoolPref(arguments[1]),
                  menuPref:Services.prefs.getBoolPref(arguments[2])};
                """, script_args=[PREFERENCE, CONTEXT_PREFERENCE, MENU_PREFERENCE], sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova or metadata['iconPref'] != enabled or metadata['contextPref'] != context_enabled
                    or metadata['menuPref'] != menu_enabled):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            installed = m.execute_async_script("""
                const done=arguments[arguments.length-1];
                const file=Cc['@mozilla.org/file/local;1'].createInstance(Ci.nsIFile);
                file.initWithPath(arguments[0]);
                AddonManager.installTemporaryAddon(file).then(a=>done({id:a.id,active:a.isActive}),e=>done({error:String(e)}));
                """, script_args=[str(addon)], sandbox='system')
            if installed.get('id') != EXTENSION_ID or not installed.get('active'):
                raise RuntimeError(f'temporary extension did not install: {installed}')
            widget_id = m.execute_script("""
                const {ExtensionCommon}=ChromeUtils.importESModule('resource://gre/modules/ExtensionCommon.sys.mjs');
                return ExtensionCommon.makeWidgetId(arguments[0])+'-browser-action';
                """, script_args=[EXTENSION_ID], sandbox='system')
            m.execute_script('CustomizableUI.addWidgetToArea(arguments[0], CustomizableUI.AREA_NAVBAR)',
                             script_args=[widget_id], sandbox='system')
            deadline = time.monotonic() + 20
            while True:
                sample = m.execute_script("""
                    const button=document.querySelector('#nav-bar .webextension-browser-action[data-extensionid="uBlock0@raymondhill.net"]');
                    const icon=button?.querySelector('.toolbarbutton-icon');
                    const badge=button?.querySelector('.toolbarbutton-badge');
                    if (!icon || !badge) return null;
                    const s=getComputedStyle(icon),r=icon.getBoundingClientRect();
                    const bs=getComputedStyle(badge),br=badge.getBoundingClientRect();
                    return {id:button.id, widget:button.closest('toolbaritem')?.id, image:s.listStyleImage, display:s.display,
                      visibility:s.visibility,width:r.width,height:r.height,
                      badge:{text:badge.textContent,display:bs.display,visibility:bs.visibility,width:br.width,height:br.height},
                      legacy:matchMedia('(-moz-bool-pref: "shyfox.enable.ext.mono.toolbar.icons")').matches,
                      modern:matchMedia('-moz-pref("shyfox.enable.ext.mono.toolbar.icons")').matches};
                    """, sandbox='system')
                if sample and sample['badge']['text'] == '7' and sample['width'] > 0 and sample['height'] > 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'extension toolbar fixture unavailable: {sample}')
                time.sleep(.1)
            if (sample['modern'] != enabled or sample['widget'] != widget_id
                    or sample['badge']['display'] == 'none' or sample['badge']['visibility'] != 'visible'
                    or sample['badge']['width'] <= 0 or sample['badge']['height'] <= 0):
                raise RuntimeError(f'extension selector or preference control failed: {sample}, expected {widget_id}')
            target = (profile / 'chrome/icons/ublock.svg').as_uri()
            script = """
                const priority=arguments[1] ? ' !important' : '';
                const css=`@media -moz-pref("shyfox.enable.ext.mono.toolbar.icons") { .webextension-browser-action[data-extensionid="uBlock0@raymondhill.net"] .toolbarbutton-icon { list-style-image: url("${arguments[0]}")${priority}; } }`;
                const uri='data:text/css,'+encodeURIComponent(css);
                window.windowUtils.loadSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET);
                try { return getComputedStyle(document.querySelector('#nav-bar .webextension-browser-action[data-extensionid="uBlock0@raymondhill.net"] .toolbarbutton-icon')).listStyleImage; }
                finally { window.windowUtils.removeSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET); }
                """
            control = {priority:m.execute_script(script, script_args=[target, important], sandbox='system')
                       for priority, important in (('normal', False), ('important', True))}
            if any((target in value) != enabled for value in control.values()):
                raise RuntimeError(f'extension icon intervention inconclusive: {sample}, {control}')
            badge_control = m.execute_script("""
                const css='@media -moz-pref("shyfox.enable.ext.mono.toolbar.icons") { .toolbarbutton-badge { display:none; } }';
                const uri='data:text/css,'+encodeURIComponent(css);
                window.windowUtils.loadSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET);
                try { return getComputedStyle(document.querySelector('#nav-bar .webextension-browser-action[data-extensionid="uBlock0@raymondhill.net"] .toolbarbutton-badge')).display; }
                finally { window.windowUtils.removeSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET); }
                """, sandbox='system')
            if (badge_control == 'none') != enabled:
                raise RuntimeError(f'extension badge intervention inconclusive: {sample}, {badge_control}')
            m.set_context('content')
            m.navigate('data:text/html,<a href="https://example.invalid/">textfox extension link fixture</a>')
            link=m.find_element('css selector', 'a')
            m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(link, button=2).perform()
            m.set_context('chrome')
            deadline=time.monotonic()+15
            while True:
                context=m.execute_script("""
                    const popup=document.getElementById('contentAreaContextMenu');
                    const item=popup.querySelector('menuitem[id*="ublock0_raymondhill_net-menuitem-_uBlock0-"]');
                    if (popup.state !== 'open' || !item || item.hidden) return null;
                    const s=getComputedStyle(item),r=item.getBoundingClientRect();
                    return {id:item.id,image:s.backgroundImage,width:r.width,height:r.height,
                      legacy:matchMedia('(-moz-bool-pref: "shyfox.enable.ext.mono.context.icons")').matches,
                      modern:matchMedia('-moz-pref("shyfox.enable.ext.mono.context.icons")').matches};
                    """, sandbox='system')
                if context and context['width'] > 0 and context['height'] > 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'extension context-menu fixture unavailable: {context}')
                time.sleep(.1)
            context_control=m.execute_script("""
                const css=`@media -moz-pref("shyfox.enable.ext.mono.context.icons") { #contentAreaContextMenu menuitem[id*="ublock0_raymondhill_net-menuitem-_uBlock0-"] { background-image: url("${arguments[0]}") !important; } }`;
                const uri='data:text/css,'+encodeURIComponent(css);
                window.windowUtils.loadSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET);
                try { return getComputedStyle(document.querySelector('#contentAreaContextMenu menuitem[id*="ublock0_raymondhill_net-menuitem-_uBlock0-"]')).backgroundImage; }
                finally { window.windowUtils.removeSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET); }
                """, script_args=[target], sandbox='system')
            if context['modern'] != context_enabled or (target in context_control) != context_enabled:
                raise RuntimeError(f'extension context icon intervention inconclusive: {context}, {context_control}')
            m.actions.sequence('key', 'keyboard').send_keys(Keys.ESCAPE).perform()
            deadline = time.monotonic() + 10
            while m.execute_script("return document.getElementById('contentAreaContextMenu').state", sandbox='system') != 'closed':
                if time.monotonic() >= deadline:
                    raise RuntimeError('Escape did not close the extension context menu')
                time.sleep(.1)
            button=m.find_element('css selector', '#nav-bar .webextension-browser-action[data-extensionid="uBlock0@raymondhill.net"]')
            m.actions.sequence('pointer','mouse',{'pointerType':'mouse'}).click(button,button=2).perform()
            deadline=time.monotonic()+15
            while True:
                toolbar_menu=m.execute_script("""
                    const popup=document.getElementById('toolbar-context-menu');
                    const item=popup.querySelector('.customize-context-manageExtension');
                    if (popup.state !== 'open' || !item || item.hidden) return null;
                    const r=item.getBoundingClientRect(); if(r.width <= 0 || r.height <= 0) return null;
                    const selector=':is(.customize-context-manageExtension,.customize-context-removeExtension,.customize-context-reportExtension,.customize-context-moveToPanel,.customize-context-removeFromToolbar,#toggle_PersonalToolbar,.viewCustomizeToolbar)';
                    return {legacy:matchMedia('(-moz-bool-pref: "shyfox.enable.context.menu.icons")').matches,
                      modern:matchMedia('-moz-pref("shyfox.enable.context.menu.icons")').matches,
                      items:[...popup.querySelectorAll(selector)].map(e=>{
                        const s=getComputedStyle(e),r=e.getBoundingClientRect();
                        return {id:e.id,classes:e.className,hidden:e.hidden,disabled:e.disabled,
                          display:s.display,visibility:s.visibility,width:r.width,height:r.height,image:s.backgroundImage};
                      })};
                    """, sandbox='system')
                if toolbar_menu:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'extension toolbar context menu unavailable: {toolbar_menu}')
                time.sleep(.1)
            if toolbar_menu['modern'] != menu_enabled:
                raise RuntimeError(f'toolbar context preference control failed: {toolbar_menu}')
            selector_results=selector_query(m,selector_records) if selector_query else []
            if selector_query and selector_results[0]['status']!='structural-match':
                raise RuntimeError(f'toolbar bookmark-toggle selector did not match: {selector_results}')
            toolbar_control=m.execute_script("""
                const item=document.querySelector('#toolbar-context-menu .customize-context-manageExtension');
                const css='@media -moz-pref("shyfox.enable.context.menu.icons") { #toolbar-context-menu .customize-context-manageExtension { background-image: url("chrome://global/skin/icons/settings.svg") !important; } }';
                const uri='data:text/css,'+encodeURIComponent(css);
                window.windowUtils.loadSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET);
                try { return getComputedStyle(item).backgroundImage; }
                finally { window.windowUtils.removeSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET); }
                """, sandbox='system')
            expected='url("chrome://global/skin/icons/settings.svg")' if menu_enabled else 'none'
            if toolbar_control != expected:
                raise RuntimeError(f'toolbar context icon intervention failed: {toolbar_control}')
            m.find_element('id','toolbar-context-customize').click()
            deadline=time.monotonic()+15
            while not m.execute_script("return document.documentElement.hasAttribute('customizing')",sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('toolbar customize action did not enter customization')
                time.sleep(.1)
            print(json.dumps({'firefox':metadata,'extension':installed,'sample':sample,'control':control,
                              'badgeControl':badge_control,'context':context,'contextControl':context_control,
                              'toolbarContext':toolbar_menu,'toolbarControl':toolbar_control,
                              'customizeActionEntered':True,'selectorRecords':selector_results}), flush=True)
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
            for context_enabled in (False, True):
                for menu_enabled in (False, True):
                    check(nova, enabled, context_enabled, menu_enabled)
