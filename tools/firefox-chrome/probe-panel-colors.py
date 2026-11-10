#!/usr/bin/env python3
"""Inspect app-menu colors before and after a temporary background override."""
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


def check(nova, color_preference, activation):
    selector_path=os.environ.get('TEXTFOX_SELECTOR_INVENTORY')
    selector_records=None
    selector_query=None
    if selector_path:
        selector_records=[r for r in json.loads(Path(selector_path).read_text()) if r['file']=='chrome/menus.css']
        if not selector_records:
            raise ValueError('selector inventory has no menu records')
        selector_query=runpy.run_path(str(ROOT / 'tools/firefox-chrome/probe-selector-dom.py'))['query']
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-panel-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(
            ':root { --tf-bg: rgb(23, 47, 71); --tf-accent: rgb(19, 83, 137); '
            '--tf-border: rgb(71, 31, 111); --tf-border-width: 5px; --tf-rounding: 7px; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False,
                 'browser.display.document_color_use': color_preference}
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
            metadata = m.execute_script("return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, build:Services.appinfo.appBuildID, nova:Services.prefs.getBoolPref('browser.nova.enabled'), documentColorUse:Services.prefs.getIntPref('browser.display.document_color_use'), private:PrivateBrowsingUtils.isWindowPrivate(window)}", sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova or metadata['documentColorUse'] != color_preference or metadata['private']):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            m.execute_script('PanelUI.show()', sandbox='system')
            deadline = time.monotonic() + 20
            while not m.execute_script("return document.getElementById('appMenu-popup').state == 'open'", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('app menu did not open')
                time.sleep(.2)
            result = m.execute_script("""
                const panel = document.getElementById('appMenu-popup');
                const slot = panel.shadowRoot?.querySelector('slot[part="content"]');
                if (!slot) throw new Error('app menu content slot not found');
                const s = getComputedStyle(slot), ps=getComputedStyle(panel);
                const root = getComputedStyle(document.documentElement);
                const panelTokens=['--panel-background','--panel-background-color',
                  '--panel-border-radius','--panel-border-color','--panel-separator-color'];
                const properties = ['--lwt-text-color', '--toolbar-color', '--panel-text-color', '--tf-bg'];
                const privateButton = document.getElementById('appMenu-new-private-window-button2');
                if (!privateButton) throw new Error('private window menu item not found');
                const rect = privateButton.getBoundingClientRect(), buttonStyle = getComputedStyle(privateButton);
                const pseudoStyle=getComputedStyle(privateButton,'::before');
                const iconIds=['appMenu-new-tab-button2','appMenu-new-window-button2',
                  'appMenu-new-private-window-button2','appMenu-bookmarks-button','appMenu-history-button',
                  'appMenu-downloads-button','appMenu-passwords-button','appMenu-extensions-themes-button',
                  'appMenu-print-button2','appMenu-save-file-button2','appMenu-find-button2',
                  'appMenu-translate-button','appMenu-settings-button','appMenu-more-button2',
                  'appMenu-help-button2','appMenu-quit-button2'];
                const icons=Object.fromEntries(iconIds.map(id=>{
                  const e=document.getElementById(id);
                  if (!e) return [id,null];
                  const st=getComputedStyle(e), r=e.getBoundingClientRect();
                  return [id,{image:st.listStyleImage,display:st.display,visibility:st.visibility,
                    width:r.width,height:r.height,inPanel:panel.contains(e)}];
                }));
                return {icons, background:s.backgroundColor, color:s.color, border:s.border,
                        radius:s.borderRadius, forcedColors:matchMedia('(forced-colors: active)').matches,
                        panelTokens:Object.fromEntries(panelTokens.map(p=>[p,ps.getPropertyValue(p).trim()])),
                        slotTokens:Object.fromEntries(panelTokens.map(p=>[p,s.getPropertyValue(p).trim()])),
                        shadowSelector:{lightMatches:document.querySelectorAll('#appMenu-popup #shadow-root > *').length,
                          shadowIdPresent:!!panel.shadowRoot.querySelector('#shadow-root'),
                          contentSlotPart:slot.getAttribute('part')},
                        privateButton:{image:buttonStyle.listStyleImage, display:buttonStyle.display,
                          width:rect.width, height:rect.height,
                          pseudoFillOpacity:pseudoStyle.fillOpacity,
                          pseudoOpacityToken:pseudoStyle.getPropertyValue('--toolbarbutton-icon-fill-opacity').trim()},
                        rootProperties:Object.fromEntries(properties.map(p => [p, root.getPropertyValue(p).trim()]))};
                """, sandbox='system')
            selector_states={}
            if selector_query:
                selector_states['document']=selector_query(m,selector_records)
                selector_states['panelShadow']=selector_query(m,selector_records,shadow_host='#appMenu-popup')
                slot=[r for r in selector_states['panelShadow'] if r['selector']=='slot[part="content"]']
                if len(slot)!=1 or slot[0]['status']!='structural-match':
                    raise RuntimeError(f'panel content slot selector did not match: {slot}')
            if (result['panelTokens']['--panel-background'] != 'rgb(23, 47, 71)'
                    or result['panelTokens']['--panel-border-radius'] != '7px'
                    or result['panelTokens']['--panel-separator-color'] != 'rgb(71, 31, 111)'
                    or result['radius'] != '7px'):
                raise RuntimeError(f'configured panel tokens or radius missing: {result}')
            if any(icon is None or not icon['inPanel'] or icon['image'] == 'none'
                   for icon in result['icons'].values()):
                raise RuntimeError(f'app-menu icon fixture is incomplete: {result["icons"]}')
            icon_resources = m.execute_async_script("""
                const done=arguments[arguments.length-1];
                const ids=[...arguments[0], 'textfox-negative-control'];
                Promise.all(ids.map(id => new Promise(resolve => {
                  const raw=id === 'textfox-negative-control'
                    ? 'url("chrome://global/skin/icons/textfox-nonexistent-resource.svg")'
                    : getComputedStyle(document.getElementById(id)).listStyleImage;
                  const match=raw.match(/^url\\(["']?(.*?)["']?\\)$/);
                  if (!match) { resolve({id,raw,loaded:false,reason:'not a single URL'}); return; }
                  const image=new Image();
                  const timer=setTimeout(()=>resolve({id,raw,loaded:false,reason:'timeout'}),5000);
                  image.onload=()=>{ clearTimeout(timer); resolve({id,raw,loaded:true,width:image.naturalWidth,height:image.naturalHeight}); };
                  image.onerror=()=>{ clearTimeout(timer); resolve({id,raw,loaded:false,reason:'load error'}); };
                  image.src=match[1];
                }))).then(done);
                """, script_args=[list(result['icons'])], sandbox='system')
            control = next(item for item in icon_resources if item['id'] == 'textfox-negative-control')
            if control['loaded'] or control.get('reason') != 'load error':
                raise RuntimeError(f'icon resource negative control failed: {control}')
            if any(not item['loaded'] or item.get('width', 0) <= 0 or item.get('height', 0) <= 0
                   for item in icon_resources if item['id'] != 'textfox-negative-control'):
                raise RuntimeError(f'app-menu icon resource failed to load: {icon_resources}')
            opacity_control = m.execute_script("""
                const e=document.getElementById('appMenu-new-private-window-button2');
                e.style.setProperty('--toolbarbutton-icon-fill-opacity','0.37','important');
                try { return getComputedStyle(e,'::before').fillOpacity; }
                finally { e.style.removeProperty('--toolbarbutton-icon-fill-opacity'); }
                """, sandbox='system')
            if opacity_control != '0.37':
                raise RuntimeError(f'app menu icon opacity intervention failed: {opacity_control}')
            button = result['privateButton']
            if (button['image'] != 'url("chrome://browser/skin/privateBrowsing.svg")'
                    or button['display'] == 'none' or button['width'] <= 0 or button['height'] <= 0):
                raise RuntimeError(f'private window menu item or icon style missing: {button}')
            image = m.execute_async_script("""
                const done = arguments[arguments.length - 1];
                const image = new Image();
                image.onload = () => done({loaded:true, width:image.naturalWidth, height:image.naturalHeight});
                image.onerror = () => done({loaded:false});
                image.src = 'chrome://browser/skin/privateBrowsing.svg';
                """, sandbox='system')
            if not image.get('loaded') or image.get('width', 0) <= 0 or image.get('height', 0) <= 0:
                raise RuntimeError(f'private window icon resource did not load: {image}')
            override = m.execute_script("""
                const panel = document.getElementById('appMenu-popup');
                panel.style.setProperty('--panel-background-color', 'var(--tf-bg)', 'important');
                const s = getComputedStyle(panel.shadowRoot.querySelector('slot[part="content"]'));
                return {background:s.backgroundColor, color:s.color};
                """, sandbox='system')
            m.execute_script("document.getElementById('appMenu-popup').style.removeProperty('--panel-background-color')", sandbox='system')
            m.actions.sequence('key', 'keyboard').send_keys(Keys.ARROW_DOWN).perform()
            focused = m.execute_script("""
                const active = document.activeElement, panel = document.getElementById('appMenu-popup');
                const style = getComputedStyle(active);
                return {tag:active?.localName, id:active?.id, classes:active?.className,
                        inPanel:panel.contains(active), panelState:panel.state,
                        outlineStyle:style.outlineStyle, outlineWidth:style.outlineWidth};
                """, sandbox='system')
            m.actions.sequence('key', 'keyboard').send_keys(Keys.ESCAPE).perform()
            closed = m.execute_script("return document.getElementById('appMenu-popup').state", sandbox='system')
            print(json.dumps({'firefox': metadata, 'baseline': result, 'privateIconResource': image,
                              'iconResources':icon_resources, 'opacityControl':opacity_control,
                              'selectorStates':selector_states,
                              'override': override, 'keyboardFocus': focused, 'afterEscape': closed}), flush=True)
            if focused['panelState'] != 'open' or not focused['inPanel'] or closed != 'closed':
                raise RuntimeError('app menu keyboard navigation or Escape failed')
            m.execute_script('PanelUI.show()', sandbox='system')
            deadline = time.monotonic() + 20
            while m.execute_script("return document.getElementById('appMenu-popup').state", sandbox='system') != 'open':
                if time.monotonic() >= deadline:
                    raise RuntimeError('app menu did not reopen')
                time.sleep(.1)
            if m.execute_script("return [...Services.wm.getEnumerator('navigator:browser')].some(w => PrivateBrowsingUtils.isWindowPrivate(w))", sandbox='system'):
                raise RuntimeError('private window existed before menu activation')
            if activation == 'pointer':
                m.find_element('id', 'appMenu-new-private-window-button2').click()
            else:
                for _ in range(30):
                    if m.execute_script("return document.activeElement?.id === 'appMenu-new-private-window-button2'", sandbox='system'):
                        break
                    m.actions.sequence('key', 'keyboard').send_keys(Keys.ARROW_DOWN).perform()
                else:
                    raise RuntimeError('keyboard navigation did not reach private window menu item')
                m.actions.sequence('key', 'keyboard').send_keys(Keys.ENTER).perform()
            deadline = time.monotonic() + 20
            while not m.execute_script("""
                return [...Services.wm.getEnumerator('navigator:browser')]
                  .some(w => PrivateBrowsingUtils.isWindowPrivate(w));
                """, sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('app menu did not open a private window')
                time.sleep(.1)
            print(json.dumps({'nova': nova, 'documentColorUse': color_preference,
                              'activation': activation, 'privateWindowOpened': True}), flush=True)
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
        for color_preference in (0, 2):
            for activation in ('pointer', 'keyboard'):
                check(nova, color_preference, activation)
