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
            (profile / 'chrome' / 'config.css').write_text(':root { --tf-rounding: 7px; --tf-border-width: 5px; }\n')
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
                              'intervention':intervention, 'overflow':overflow_state}), flush=True)
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
