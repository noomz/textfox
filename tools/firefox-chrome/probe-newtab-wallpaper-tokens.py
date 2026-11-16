#!/usr/bin/env python3
"""Inspect new-tab wallpaper token consumers in disposable Firefox profiles.

New-tab feeds may make network requests. Requires the live-tool venv Python.
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

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
PREFIX = 'browser.newtabpage.activity-stream.'


def check(browser_nova, newtab_nova, themed, color):
    selector_path=os.environ.get('TEXTFOX_SELECTOR_INVENTORY')
    selector_records=None
    selector_query=None
    if selector_path:
        selector_records=[r for r in json.loads(Path(selector_path).read_text()) if r['file']=='chrome/content/newtab.css']
        if not selector_records:
            raise ValueError('selector inventory has no new-tab records')
        selector_query=runpy.run_path(str(ROOT / 'tools/firefox-chrome/probe-selector-dom.py'))['query']
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-wallpaper-tokens-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port':port, 'browser.nova.enabled':browser_nova,
                 PREFIX+'nova.enabled':newtab_nova,
                 PREFIX+'testing.shouldInitializeFeeds':True,
                 PREFIX+'newtabWallpapers.enabled':True,
                 PREFIX+'newtabWallpapers.user.enabled':True,
                 PREFIX+'newtabWallpapers.wallpaper':f'solid-color-picker-{color}',
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
                  browserNova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled'),
                  wallpaper:Services.prefs.getStringPref('browser.newtabpage.activity-stream.newtabWallpapers.wallpaper')};
                """, sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['browserNova'] != browser_nova or meta['newtabNova'] != newtab_nova
                    or meta['wallpaper'] != f'solid-color-picker-{color}'):
                raise RuntimeError(f'unexpected Firefox process, profile, or preferences: {meta}')
            m.execute_script('gBrowser.selectedTab=gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while True:
                state = m.execute_script("""
                    const body=document.body, host=document.querySelector('.personalize-button, .open-customization-button');
                    const button=host?.shadowRoot?.querySelector('button') || host;
                    if (location.href !== 'about:newtab' || !body || !button) return null;
                    const b=getComputedStyle(body), s=getComputedStyle(button), r=button.getBoundingClientRect();
                    return {wallpaper:b.getPropertyValue('--newtab-wallpaper-color').trim(),
                      classes:[...body.classList], buttonKind:host.classList.contains('personalize-button') ? 'classic' : 'nova',
                      hostExpanded:host.getAttribute('aria-expanded'), buttonExpanded:button.getAttribute('aria-expanded'), hostTag:host.localName,
                      buttonBackground:s.backgroundColor,
                      weatherToken:s.getPropertyValue('--newtab-weather-background-color').trim(),
                      buttonDisplay:s.display,buttonVisibility:s.visibility,width:r.width,height:r.height,
                      prefersContrast:matchMedia('(prefers-contrast)').matches};
                    """)
                if (state and state['wallpaper'].lower() == color.lower()
                        and any(c in state['classes'] for c in ('lightWallpaper','darkWallpaper'))
                        and state['width'] > 0 and state['height'] > 0):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'wallpaper fixture did not initialize: {state}')
                time.sleep(.1)
            selector_results=selector_query(m,selector_records) if selector_query else []
            if selector_query:
                expected='&.lightWallpaper' if 'lightWallpaper' in state['classes'] else '&.darkWallpaper'
                candidates=[r for r in selector_results if r['selector']==expected]
                if len(candidates)!=1 or candidates[0]['status']!='structural-match':
                    raise RuntimeError(f'wallpaper selector did not match: {candidates}')
            control = m.execute_script("""
                const host=document.querySelector('.personalize-button, .open-customization-button');
                const e=host.shadowRoot?.querySelector('button') || host;
                const names=['--newtab-weather-background-color','transition'];
                const saved=names.map(n=>({name:n,value:e.style.getPropertyValue(n),priority:e.style.getPropertyPriority(n)}));
                e.style.setProperty('transition','none','important');
                const before=getComputedStyle(e).backgroundColor;
                e.style.setProperty(names[0],'rgb(31, 91, 151)','important');
                try { const s=getComputedStyle(e); return {before,during:s.backgroundColor,
                  tokenDuring:s.getPropertyValue(names[0]).trim()}; }
                finally { for(const {name,value,priority} of saved) {
                  if(value) e.style.setProperty(name,value,priority); else e.style.removeProperty(name);
                }}
                """)
            if (state['prefersContrast'] or state['buttonKind'] != ('nova' if newtab_nova else 'classic')
                    or state['buttonExpanded'] != 'false' or control['tokenDuring'] != 'rgb(31, 91, 151)'):
                raise RuntimeError(f'wallpaper button fixture inconclusive: {state}, {control}')
            if state['buttonKind'] == 'classic' and (control['during'] != 'rgb(31, 91, 151)'
                                                     or control['before'] == control['during']):
                raise RuntimeError(f'classic wallpaper token control inconclusive: {state}, {control}')
            if themed and (state['weatherToken'] != 'transparent'
                           or (state['buttonKind'] == 'classic' and control['before'] != 'rgba(0, 0, 0, 0)')):
                raise RuntimeError(f'textfox weather token did not reach button: {state}, {control}')
            host = m.find_element('css selector', '.personalize-button, .open-customization-button')
            host.click()
            deadline = time.monotonic() + 10
            while True:
                opened = m.execute_script("""
                    const host=document.querySelector('.personalize-button, .open-customization-button');
                    const button=host?.shadowRoot?.querySelector('button') || host;
                    const menu=document.querySelector('.customize-menu');
                    const r=menu?.getBoundingClientRect();
                    return {hostPresent:!!host,hostTag:host?.localName,expanded:host?.getAttribute('aria-expanded'),
                      buttonExpanded:button?.getAttribute('aria-expanded'),open:menu?.open || false,
                      width:r?.width || 0,height:r?.height || 0,
                      display:menu && getComputedStyle(menu).display};
                    """)
                if (opened['open'] and opened['buttonExpanded'] == 'true'
                        and opened['width'] > 0 and opened['height'] > 0):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'wallpaper personalize button did not open menu: {opened}')
                time.sleep(.1)
            print(json.dumps({'firefox':meta,'themed':themed,'state':state,'control':control,
                              'customizeMenu':opened,'selectorRecords':selector_results}),flush=True)
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
    for browser_nova in (False, True):
        for newtab_nova in (False, True):
            for themed in (False, True):
                for color in ('#14283c', '#f0e0d0'):
                    check(browser_nova, newtab_nova, themed, color)
