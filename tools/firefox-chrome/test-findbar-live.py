#!/usr/bin/env python3
"""Exercise findbar keyboard search in disposable Firefox profiles."""
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


def check(nova, themed, high_contrast, rtl):
    selector_path=os.environ.get('TEXTFOX_SELECTOR_INVENTORY')
    selector_records=None
    selector_query=None
    if selector_path:
        selector_records=[r for r in json.loads(Path(selector_path).read_text()) if r['file']=='chrome/findbar.css']
        if not selector_records:
            raise ValueError('selector inventory has no findbar records')
        selector_query=runpy.run_path(str(ROOT / 'tools/firefox-chrome/probe-selector-dom.py'))['query']
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-findbar-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
            (profile / 'chrome' / 'config.css').write_text(
                ':root { --tf-text-transform: uppercase; --tf-accent: rgb(19, 83, 137); }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False,
                 'accessibility.typeaheadfind.casesensitive': 0,
                 'ui.useAccessibilityTheme': 1 if high_contrast else 0,
                 'intl.l10n.pseudo': 'bidi' if rtl else ''}
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
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled')}", sandbox='system')
            if process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve() or meta['nova'] != nova:
                raise RuntimeError('unexpected Firefox process, profile, or Nova preference')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox startup did not finish')
                time.sleep(.1)
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("data:text/html,<p>unique-find-target</p>")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 15
            while not m.execute_script("return location.href.startsWith('data:text/html') && document.body?.textContent.includes('unique-find-target')"):
                if time.monotonic() >= deadline:
                    raise RuntimeError('findbar fixture page did not load')
                time.sleep(.1)
            m.set_context('chrome')
            m.actions.sequence('key', 'keyboard').key_down(Keys.CONTROL).key_down('f').key_up('f').key_up(Keys.CONTROL).perform()
            deadline = time.monotonic() + 15
            while not m.execute_script("return !!document.querySelector('findbar:not([hidden])')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Ctrl+F did not open findbar')
                time.sleep(.1)
            field = m.find_element('css selector', 'findbar:not([hidden]) .findbar-textbox')
            field.send_keys('unique-find-target')
            m.set_context('content')
            deadline = time.monotonic() + 10
            while m.execute_script('return window.getSelection().toString()') != 'unique-find-target':
                if time.monotonic() >= deadline:
                    page = m.execute_script('return {url:location.href, text:document.body?.innerText, selection:window.getSelection().toString()}')
                    m.set_context('chrome')
                    bar = m.execute_script("return {value:document.querySelector('findbar:not([hidden])')?._findField.value, url:gBrowser.selectedBrowser.currentURI.spec}", sandbox='system')
                    raise RuntimeError(f'findbar did not select target text: {page}, {bar}')
                time.sleep(.1)
            m.set_context('chrome')
            selector_states={}
            if selector_query:
                selector_states['open']=selector_query(m,selector_records)
            result = m.execute_script("""
                const bar = document.querySelector('findbar:not([hidden])'), s = getComputedStyle(bar);
                return {visible:!bar.hidden && s.display !== 'none', fieldValue:bar._findField.value,
                        focused:document.activeElement === bar._findField || bar._findField.matches(':focus-within'),
                        prefersContrast:matchMedia('(prefers-contrast)').matches,
                        direction:getComputedStyle(document.documentElement).direction,
                        outlineStyle:getComputedStyle(bar._findField).outlineStyle,
                        outlineWidth:getComputedStyle(bar._findField).outlineWidth,
                        fieldBackground:getComputedStyle(bar._findField).backgroundColor,
                        checkboxes:[...bar.querySelectorAll('.findbar-container > checkbox')].map(e=>{
                          const s=getComputedStyle(e), r=e.getBoundingClientRect();
                          return {transform:s.textTransform, display:s.display, width:r.width, height:r.height};
                        })};
                """, sandbox='system')
            print(json.dumps({'firefox': meta, 'themed': themed, 'result': result}), flush=True)
            if result['prefersContrast'] != high_contrast or result['direction'] != ('rtl' if rtl else 'ltr'):
                raise RuntimeError('requested contrast or direction not active')
            if not result['visible'] or result['fieldValue'] != 'unique-find-target' or not result['focused']:
                raise RuntimeError('findbar state does not reflect keyboard search')
            if result['outlineStyle'] == 'none' or result['outlineWidth'] == '0px':
                raise RuntimeError('focused findbar field has no computed outline')
            if themed:
                hover = m.execute_script("""
                    const bar=document.querySelector('findbar:not([hidden])');
                    const measure=()=>{ const s=getComputedStyle(bar), p=getComputedStyle(bar,'::before');
                      return {matches:bar.matches(':hover'), border:s.borderTopColor,
                        title:p.content, titleDisplay:p.display, titleColor:p.color}; };
                    const before=measure();
                    InspectorUtils.addPseudoClassLock(bar, ':hover');
                    const active=measure();
                    InspectorUtils.removePseudoClassLock(bar, ':hover');
                    return {before,active};
                    """, sandbox='system')
                print(json.dumps({'nova':nova, 'rtl':rtl, 'highContrast':high_contrast,
                                  'findbarHover':hover}), flush=True)
                if (not hover['active']['matches'] or hover['active']['title'] != '"find"'
                        or hover['active']['titleDisplay'] != 'flex'
                        or hover['active']['border'] != 'rgb(19, 83, 137)'
                        or hover['active']['titleColor'] != 'rgb(19, 83, 137)'):
                    raise RuntimeError(f'findbar hover styles did not apply: {hover}')
            bar=m.find_element('css selector','findbar:not([hidden])')
            m.actions.sequence('pointer','mouse',{'pointerType':'mouse'}).pointer_move(0,0,origin=bar).perform()
            deadline=time.monotonic()+10
            while True:
                pointer_hover=m.execute_script("""
                    const e=document.querySelector('findbar:not([hidden])');
                    return {matches:e.matches(':hover'),border:getComputedStyle(e).borderTopColor,
                      title:getComputedStyle(e,'::before').color};
                    """,sandbox='system')
                if pointer_hover['matches'] and (not themed or (pointer_hover['border']=='rgb(19, 83, 137)' and pointer_hover['title']=='rgb(19, 83, 137)')):
                    break
                if time.monotonic()>=deadline:
                    raise RuntimeError(f'findbar pointer hover did not settle: {pointer_hover}')
                time.sleep(.1)
            if selector_query:
                selector_states['hover']=selector_query(m,selector_records)
            visible_checkboxes = [c for c in result['checkboxes'] if c['display'] != 'none' and c['width'] > 0 and c['height'] > 0]
            if not visible_checkboxes:
                raise RuntimeError('findbar checkbox fixture is unavailable')
            if themed and any(c['transform'] != 'uppercase' for c in visible_checkboxes):
                raise RuntimeError(f'findbar checkbox text transform did not apply: {visible_checkboxes}')
            field.send_keys(Keys.CONTROL, 'a')
            field.send_keys('UNIQUE-FIND-TARGET')
            case_checkbox = m.find_element('css selector', 'findbar:not([hidden]) .findbar-case-sensitive')
            if m.execute_script("return document.querySelector('findbar:not([hidden]) .findbar-case-sensitive').checked", sandbox='system'):
                raise RuntimeError('case-sensitive fixture started enabled')
            case_checkbox.click()
            deadline = time.monotonic() + 10
            while not m.execute_script("const b=document.querySelector('findbar:not([hidden])'); return b.querySelector('.findbar-case-sensitive').checked && b._findField.getAttribute('status') === 'notfound'", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('match-case control did not produce a case-sensitive miss')
                time.sleep(.1)
            case_checkbox.click()
            deadline = time.monotonic() + 10
            while m.execute_script("const b=document.querySelector('findbar:not([hidden])'); return b.querySelector('.findbar-case-sensitive').checked || b._findField.getAttribute('status') === 'notfound'", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('disabling match-case did not restore the match')
                time.sleep(.1)
            m.set_context('content')
            deadline = time.monotonic() + 10
            while m.execute_script('return window.getSelection().toString()') != 'unique-find-target':
                if time.monotonic() >= deadline:
                    raise RuntimeError('case-insensitive search did not select the matching text')
                time.sleep(.1)
            m.set_context('chrome')
            field.send_keys(Keys.CONTROL, 'a')
            field.send_keys('UNIQUE-FIND-TARGET')
            field.click()
            for tabs in range(20):
                focus = m.execute_script("""
                    const e=document.querySelector('findbar:not([hidden]) .findbar-case-sensitive');
                    const check=e.querySelector('.checkbox-check');
                    if (!check) throw new Error('Match Case checkmark missing');
                    const s=getComputedStyle(e), cs=getComputedStyle(check), r=check.getBoundingClientRect();
                    return {focused:e.matches(':focus-within'), visible:e.matches(':focus-visible'),
                      outlineStyle:s.outlineStyle, outlineWidth:s.outlineWidth,
                      checkOutlineStyle:cs.outlineStyle,checkOutlineWidth:cs.outlineWidth,
                      checkWidth:r.width,checkHeight:r.height,
                      checkScreenX:window.mozInnerScreenX+r.x,checkScreenY:window.mozInnerScreenY+r.y,
                      devicePixelRatio:window.devicePixelRatio};
                    """, sandbox='system')
                if focus['focused']:
                    break
                m.actions.sequence('key', 'keyboard').send_keys(Keys.TAB).perform()
            else:
                raise RuntimeError(f'keyboard Tab did not reach Match Case: {focus}')
            if (not focus['visible'] or focus['checkOutlineStyle'] == 'none'
                    or focus['checkOutlineWidth'] == '0px' or focus['checkWidth'] <= 0 or focus['checkHeight'] <= 0):
                raise RuntimeError(f'Match Case keyboard focus indicator unavailable: {focus}')
            paint_dir = os.environ.get('TEXTFOX_FINDBAR_PAINT_DIR')
            paint_files = {}
            def capture(phase):
                if paint_dir:
                    time.sleep(.25)
                    path = Path(paint_dir) / f'findbar-{int(nova)}-{int(themed)}-{int(high_contrast)}-{int(rtl)}-{phase}.png'
                    subprocess.run(['grim', str(path)], check=True, stdout=subprocess.DEVNULL)
                    paint_files[phase] = str(path)
            capture('focused')
            capture('focused-control')
            m.actions.sequence('key', 'keyboard').send_keys(Keys.TAB).perform()
            blurred = m.execute_script("""
                const e=document.querySelector('findbar:not([hidden]) .findbar-case-sensitive');
                return {focused:e.matches(':focus-within'),
                  checkOutlineStyle:getComputedStyle(e.querySelector('.checkbox-check')).outlineStyle};
                """, sandbox='system')
            if blurred['focused'] or blurred['checkOutlineStyle'] != 'none':
                raise RuntimeError(f'Match Case focus outline did not clear: {blurred}')
            capture('blurred')
            m.actions.sequence('key', 'keyboard').key_down(Keys.SHIFT).send_keys(Keys.TAB).key_up(Keys.SHIFT).perform()
            restored = m.execute_script("""
                const e=document.querySelector('findbar:not([hidden]) .findbar-case-sensitive');
                return {focused:e.matches(':focus-visible'),
                  checkOutlineStyle:getComputedStyle(e.querySelector('.checkbox-check')).outlineStyle};
                """, sandbox='system')
            if not restored['focused'] or restored['checkOutlineStyle'] != focus['checkOutlineStyle']:
                raise RuntimeError(f'Match Case focus outline did not return: {restored}')
            capture('restored')
            m.actions.sequence('key', 'keyboard').send_keys(Keys.SPACE).perform()
            deadline = time.monotonic() + 10
            while not m.execute_script("const b=document.querySelector('findbar:not([hidden])'); return b.querySelector('.findbar-case-sensitive').checked && b._findField.getAttribute('status') === 'notfound'", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('keyboard Match Case did not produce a case-sensitive miss')
                time.sleep(.1)
            m.actions.sequence('key', 'keyboard').send_keys(Keys.SPACE).perform()
            deadline = time.monotonic() + 10
            while m.execute_script("const b=document.querySelector('findbar:not([hidden])'); return b.querySelector('.findbar-case-sensitive').checked || b._findField.getAttribute('status') === 'notfound'", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('keyboard Match Case did not restore the match')
                time.sleep(.1)
            print(json.dumps({'nova':nova, 'themed':themed, 'rtl':rtl, 'highContrast':high_contrast,
                              'keyboardCase':{'tabs':tabs, 'focus':focus, 'blurred':blurred,
                                              'restored':restored, 'paintFiles':paint_files,
                                              'toggledOnAndOff':True}}), flush=True)
            field.send_keys(Keys.CONTROL, 'a')
            field.send_keys('textfox-definitely-absent')
            deadline = time.monotonic() + 10
            while not m.execute_script("return document.querySelector('findbar:not([hidden]) .findbar-textbox')?.getAttribute('status') === 'notfound'", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('findbar did not report the absent text')
                time.sleep(.1)
            deadline = time.monotonic() + 10
            while not m.execute_script("const e=document.querySelector('findbar:not([hidden]) .findbar-find-status'); return !!e?.textContent.trim() && e.getBoundingClientRect().width > 0", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('findbar not-found status text did not appear')
                time.sleep(.1)
            missing = m.execute_script("""
                const bar=document.querySelector('findbar:not([hidden])');
                const field=bar.querySelector('.findbar-textbox');
                const status=bar.querySelector('.findbar-find-status');
                const fs=getComputedStyle(field), ss=getComputedStyle(status), rect=status.getBoundingClientRect();
                return {value:field.value, fieldStatus:field.getAttribute('status'),
                  fieldBackground:fs.backgroundColor, fieldColor:fs.color,
                  label:status.textContent, labelStatus:status.getAttribute('status'),
                  labelDisplay:ss.display, labelVisibility:ss.visibility, labelWidth:rect.width, labelHeight:rect.height};
                """, sandbox='system')
            print(json.dumps({'nova':nova, 'themed':themed, 'notFound':missing}), flush=True)
            if (missing['value'] != 'textfox-definitely-absent' or missing['labelStatus'] != 'notfound'
                    or missing['labelDisplay'] == 'none' or missing['labelVisibility'] != 'visible'
                    or missing['labelWidth'] <= 0 or missing['labelHeight'] <= 0):
                raise RuntimeError(f'findbar not-found feedback is unavailable: {missing}')
            field.send_keys(Keys.CONTROL, 'a')
            field.send_keys('unique-find-target')
            deadline = time.monotonic() + 10
            while m.execute_script("return document.querySelector('findbar:not([hidden]) .findbar-textbox')?.getAttribute('status') === 'notfound'", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('findbar did not clear not-found status after a match')
                time.sleep(.1)
            m.set_context('content')
            deadline = time.monotonic() + 10
            while m.execute_script('return window.getSelection().toString()') != 'unique-find-target':
                if time.monotonic() >= deadline:
                    raise RuntimeError('findbar did not select the matching text after a miss')
                time.sleep(.1)
            m.set_context('chrome')
            m.execute_script("document.querySelector('findbar:not([hidden])').setAttribute('data-textfox-probe','true')", sandbox='system')
            m.actions.sequence('key', 'keyboard').send_keys(Keys.ESCAPE).perform()
            hidden = m.execute_script("""
                const bar=document.querySelector('findbar[data-textfox-probe]');
                if (!bar) return null;
                const r=bar.getBoundingClientRect();
                return {hidden:bar.hidden, display:getComputedStyle(bar).display, width:r.width, height:r.height};
                """, sandbox='system')
            if selector_query:
                selector_states['hidden']=selector_query(m,selector_records)
                for name,selector in (('hover','&:hover'),('hidden','&:where([hidden])')):
                    rows=[r for r in selector_states[name] if r['selector']==selector]
                    if len(rows)!=1 or rows[0]['status']!='structural-match':
                        raise RuntimeError(f'findbar {name} selector did not match: {rows}')
            print(json.dumps({'nova':nova, 'themed':themed, 'closedFindbar':hidden,
                              'pointerHover':pointer_hover,'selectorStates':selector_states}), flush=True)
            if not hidden or not hidden['hidden']:
                raise RuntimeError(f'Escape did not mark the same findbar hidden: {hidden}')
            if themed and (hidden['display'] != 'none' or hidden['width'] != 0 or hidden['height'] != 0):
                raise RuntimeError(f'textfox did not remove hidden findbar geometry: {hidden}')
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
        for themed in (False, True):
            for high_contrast in (False, True):
                for rtl in (False, True):
                    check(nova, themed, high_contrast, rtl)
