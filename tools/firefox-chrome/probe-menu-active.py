#!/usr/bin/env python3
"""Inspect keyboard-active XUL menu items in disposable Firefox profiles."""
import argparse
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


def check(nova, themed, builtin, overflow):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-menu-active-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
            (profile / 'chrome' / 'config.css').write_text(':root { --tf-rounding: 7px; --tf-border-width: 5px; --tf-border: rgb(71, 31, 111); }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
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
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,
                  version:Services.appinfo.version,build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled')};
                """, sandbox='system')
            if process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve() or metadata['nova'] != nova:
                raise RuntimeError('unexpected Firefox process, profile, or Nova preference')
            m.execute_script("""
                let popup;
                if (arguments[0]) {
                  popup = document.getElementById('toolbar-context-menu');
                } else {
                  popup = document.createXULElement('menupopup');
                  const labels = arguments[1] ? Array.from({length:80}, (_, i) => `Item ${i + 1}`) : ['First', 'Second'];
                  for (const label of labels) {
                    const item = document.createXULElement('menuitem');
                    item.setAttribute('label', label);
                    popup.append(item);
                  }
                  if (!arguments[1]) {
                    const separator=document.createXULElement('menuseparator');
                    separator.setAttribute('data-textfox-separator','true');
                    popup.append(separator);
                    const disabled=document.createXULElement('menuitem');
                    disabled.setAttribute('label','Disabled fixture');
                    disabled.setAttribute('disabled','true');
                    disabled.setAttribute('data-textfox-disabled','true');
                    popup.append(disabled);
                  }
                  document.getElementById('mainPopupSet').append(popup);
                }
                popup.setAttribute('data-textfox-probe', 'true');
                popup.openPopup(document.getElementById('PanelUI-menu-button'), 'after_end');
                """, script_args=[builtin, overflow], sandbox='system')
            deadline = time.monotonic() + 15
            while not m.execute_script("return document.querySelector('menupopup[data-textfox-probe]').state === 'open'", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('fixture menu did not open')
                time.sleep(.1)
            m.actions.sequence('key', 'keyboard').send_keys(Keys.ARROW_DOWN).perform()
            deadline = time.monotonic() + 10
            while True:
                sample = m.execute_script("""
                    const menu = document.querySelector('menupopup[data-textfox-probe]');
                    const active = menu.querySelector('menuitem[_moz-menuactive="true"]');
                    if (!active) return null;
                    const s = getComputedStyle(active), r = active.getBoundingClientRect();
                    const root = getComputedStyle(document.documentElement);
                    return {label:active.getAttribute('label'), background:s.backgroundColor,
                      color:s.color, width:r.width, height:r.height, radius:s.borderTopLeftRadius,
                      selectedToken:s.getPropertyValue('--tab-selected-bgcolor').trim(),
                      fieldColor:root.getPropertyValue('--toolbar-field-color').trim(),
                      toolbarColor:root.getPropertyValue('--toolbar-bgcolor').trim()};
                    """, sandbox='system')
                if sample:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('keyboard did not activate a menu item')
                time.sleep(.1)
            if sample['width'] <= 0 or sample['height'] <= 0:
                raise RuntimeError(f'active menu item has no geometry: {sample}')
            if themed and sample['radius'] != '7px':
                raise RuntimeError(f'configured menu item radius did not apply: {sample}')
            intervention = None
            if themed:
                intervention = m.execute_script("""
                    const active = document.querySelector('menupopup[data-textfox-probe] menuitem[_moz-menuactive="true"]');
                    active.style.setProperty('--tab-selected-bgcolor', 'Highlight', 'important');
                    const style = getComputedStyle(active);
                    const result = {background:style.backgroundColor, color:style.color,
                      selectedToken:style.getPropertyValue('--tab-selected-bgcolor').trim()};
                    active.style.removeProperty('--tab-selected-bgcolor');
                    return result;
                    """, sandbox='system')
            disabled_state = None
            separator_state = None
            builtin_separators = None
            builtin_separator_control = None
            if builtin:
                builtin_separators = m.execute_script("""
                    return [...document.querySelectorAll('menupopup[data-textfox-probe] menuseparator')].map(e=>{
                      const r=e.getBoundingClientRect(), s=getComputedStyle(e), p=getComputedStyle(e,'::before');
                      return {id:e.id,hidden:e.hidden,width:r.width,height:r.height,display:s.display,
                        visibility:s.visibility,token:s.getPropertyValue('--panel-separator-color').trim(),
                        pseudoBorderStyle:p.borderTopStyle,pseudoBorderWidth:p.borderTopWidth,
                        pseudoBorderColor:p.borderTopColor};
                    });
                    """, sandbox='system')
                visible_separators = [s for s in builtin_separators if not s['hidden'] and s['display'] != 'none'
                                      and s['visibility'] == 'visible' and s['width'] > 0 and s['height'] > 0]
                if not visible_separators:
                    raise RuntimeError(f'built-in menu has no visible separator: {builtin_separators}')
                if themed and any(s['token'] != 'rgb(71, 31, 111)' or s['pseudoBorderColor'] != 'rgb(71, 31, 111)'
                                  or s['pseudoBorderStyle'] != 'solid' or s['pseudoBorderWidth'] == '0px'
                                  for s in visible_separators):
                    raise RuntimeError(f'built-in separator did not follow configured border: {visible_separators}')
                builtin_separator_control = m.execute_script("""
                    const e=[...document.querySelectorAll('menupopup[data-textfox-probe] menuseparator')]
                      .find(e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect();
                        return !e.hidden && s.display !== 'none' && s.visibility === 'visible' && r.width > 0 && r.height > 0;});
                    const before=getComputedStyle(e,'::before').borderTopColor;
                    e.style.setProperty('--panel-separator-color','rgb(20, 90, 150)','important');
                    try { return {before,during:getComputedStyle(e,'::before').borderTopColor}; }
                    finally { e.style.removeProperty('--panel-separator-color'); }
                    """, sandbox='system')
                if (builtin_separator_control['during'] != 'rgb(20, 90, 150)'
                        or builtin_separator_control['before'] == builtin_separator_control['during']):
                    raise RuntimeError(f'built-in separator token intervention inconclusive: {builtin_separator_control}')
            if not builtin and not overflow:
                separator_state = m.execute_script("""
                    const e=document.querySelector('menupopup[data-textfox-probe] menuseparator[data-textfox-separator]');
                    const r=e.getBoundingClientRect(), s=getComputedStyle(e), p=getComputedStyle(e,'::before');
                    return {width:r.width,height:r.height,display:s.display,
                      token:s.getPropertyValue('--panel-separator-color').trim(),
                      pseudoContent:p.content,pseudoBorderStyle:p.borderTopStyle,
                      pseudoBorderWidth:p.borderTopWidth,pseudoBorderColor:p.borderTopColor};
                    """, sandbox='system')
                if (separator_state['width'] <= 0 or separator_state['height'] <= 0
                        or separator_state['pseudoBorderStyle'] != 'solid' or separator_state['pseudoBorderWidth'] == '0px'):
                    raise RuntimeError(f'menu separator fixture unavailable: {separator_state}')
                if themed and (separator_state['token'] != 'rgb(71, 31, 111)'
                               or separator_state['pseudoBorderColor'] != 'rgb(71, 31, 111)'):
                    raise RuntimeError(f'menu separator did not follow configured border: {separator_state}')
                disabled=m.find_element('css selector', 'menupopup[data-textfox-probe] menuitem[data-textfox-disabled]')
                m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).pointer_move(0, 0, origin=disabled).perform()
                deadline = time.monotonic() + 10
                while True:
                    disabled_state=m.execute_script("""
                        const e=document.querySelector('menupopup[data-textfox-probe] menuitem[data-textfox-disabled]');
                        const s=getComputedStyle(e),r=e.getBoundingClientRect();
                        return {disabled:e.disabled, hover:e.matches(':hover'), active:e.getAttribute('_moz-menuactive'),
                          background:s.backgroundColor, hoverToken:s.getPropertyValue('--tab-hover-background-color').trim(),
                          width:r.width,height:r.height};
                        """, sandbox='system')
                    if disabled_state['hover'] and disabled_state['width'] > 0 and disabled_state['height'] > 0:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'disabled menu hover fixture unavailable: {disabled_state}')
                    time.sleep(.1)
                if not disabled_state['disabled']:
                    raise RuntimeError(f'disabled menu fixture unexpectedly enabled: {disabled_state}')
                if themed and disabled_state['active'] == 'true':
                    disabled_state['control'] = m.execute_script("""
                        const e=document.querySelector('menupopup[data-textfox-probe] menuitem[data-textfox-disabled]');
                        e.style.setProperty('--tab-hover-background-color','Highlight','important');
                        try { return getComputedStyle(e).backgroundColor; }
                        finally { e.style.removeProperty('--tab-hover-background-color'); }
                        """, sandbox='system')
                    if disabled_state['control'] == disabled_state['background']:
                        raise RuntimeError(f'disabled menu color control inconclusive: {disabled_state}')
            overflow_state = None
            if overflow:
                overflow_state = m.execute_script("""
                    const popup=document.querySelector('menupopup[data-textfox-probe]');
                    const arrow=popup.shadowRoot?.querySelector('.menupopup-arrowscrollbox');
                    const scroll=arrow?.scrollbox;
                    if (!arrow || !scroll) throw new Error('menu overflow scrollbox missing');
                    const s=getComputedStyle(arrow), r=arrow.getBoundingClientRect();
                    return {hostBorder:s.borderTopWidth, hostBorderStyle:s.borderTopStyle,
                      hostWidth:r.width, hostHeight:r.height, scrollHeight:scroll.scrollHeight,
                      clientHeight:scroll.clientHeight, scrollTop:scroll.scrollTop};
                    """, sandbox='system')
                if overflow_state['scrollHeight'] <= overflow_state['clientHeight'] or overflow_state['clientHeight'] <= 0:
                    raise RuntimeError(f'menu overflow fixture is inconclusive: {overflow_state}')
                if themed and (overflow_state['hostBorder'] != '5px' or overflow_state['hostBorderStyle'] != 'solid'):
                    raise RuntimeError(f'configured menu scrollbox border missing: {overflow_state}')
                m.actions.sequence('key', 'keyboard').send_keys(Keys.END).perform()
                deadline = time.monotonic() + 10
                while True:
                    scrolled = m.execute_script("""
                        const box=document.querySelector('menupopup[data-textfox-probe]').shadowRoot
                          .querySelector('.menupopup-arrowscrollbox').scrollbox;
                        return box.scrollTop;
                        """, sandbox='system')
                    if scrolled > overflow_state['scrollTop']:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'keyboard did not scroll overflow menu: {overflow_state}, {scrolled}')
                    time.sleep(.1)
                overflow_state['afterEnd'] = scrolled
            m.actions.sequence('key', 'keyboard').send_keys(Keys.ESCAPE).perform()
            deadline = time.monotonic() + 10
            while m.execute_script("return document.querySelector('menupopup[data-textfox-probe]').state", sandbox='system') != 'closed':
                if time.monotonic() >= deadline:
                    raise RuntimeError('Escape did not close fixture menu')
                time.sleep(.1)
            print(json.dumps({'firefox':metadata, 'themed':themed, 'builtin':builtin, 'active':sample,
                              'intervention':intervention, 'disabledHover':disabled_state,
                              'separator':separator_state, 'builtinSeparators':builtin_separators,
                              'builtinSeparatorControl':builtin_separator_control, 'overflow':overflow_state}), flush=True)
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--builtin', action='store_true')
    parser.add_argument('--overflow', action='store_true')
    args = parser.parse_args()
    if args.builtin and args.overflow:
        parser.error('--overflow requires the synthetic menu')
    for nova in (False, True):
        for themed in (False, True):
            check(nova, themed, args.builtin, args.overflow)
