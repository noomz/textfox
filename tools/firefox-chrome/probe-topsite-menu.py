#!/usr/bin/env python3
"""Inspect shortcut menus in disposable Firefox profiles.

The disposable new-tab profile enables feeds and may make network requests.
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

from marionette_driver.keys import Keys

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(browser_nova, newtab_nova, themed):
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
    with tempfile.TemporaryDirectory(prefix='tf-topsite-menu-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
            (profile / 'chrome' / 'config.css').write_text(
                ':root { --tf-border-width: 7px; --tf-rounding: 3px; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': browser_nova,
                 'browser.newtabpage.activity-stream.nova.enabled': newtab_nova,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
                 'browser.newtabpage.activity-stream.widgets.enabled': True,
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
                  browserNova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled')};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['browserNova'] != browser_nova or metadata['newtabNova'] != newtab_nova):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            selector = '.top-site-outer .context-menu-button'
            deadline = time.monotonic() + 30
            while not m.execute_script('return location.href === "about:newtab" && !!document.querySelector(arguments[0])', script_args=[selector]):
                if time.monotonic() >= deadline:
                    raise RuntimeError('shortcut menu button did not initialize')
                time.sleep(.2)
            m.execute_script('document.querySelector(arguments[0]).focus()', script_args=[selector])
            m.find_element('css selector', selector).send_keys(Keys.ENTER)
            deadline = time.monotonic() + 10
            while not m.execute_script("return !!document.querySelector('.top-site-outer panel-list:popover-open')"):
                if time.monotonic() >= deadline:
                    raise RuntimeError('shortcut menu did not open')
                time.sleep(.1)
            result = m.execute_script("""
                const menu = document.querySelector('.top-site-outer panel-list:popover-open');
                const style = getComputedStyle(menu), rect = menu.getBoundingClientRect();
                const root = getComputedStyle(document.documentElement);
                return {tag:menu.localName, role:menu.getAttribute('role'),
                  oldSelectorMatches:menu.matches('.context-menu'),
                  oldSelectorAncestor:!!menu.closest('.context-menu'),
                  borderWidth:style.borderTopWidth, borderStyle:style.borderTopStyle,
                  radius:style.borderRadius, shadow:style.boxShadow,
                  configuredWidth:root.getPropertyValue('--tf-border-width').trim(),
                  configuredRounding:root.getPropertyValue('--tf-rounding').trim(),
                  width:rect.width, height:rect.height};
                """)
            if result['role'] != 'menu' or result['width'] <= 0 or result['height'] <= 0:
                raise RuntimeError(f'shortcut menu fixture is not visible: {result}')
            if themed and (result['configuredWidth'] != '7px' or result['configuredRounding'] != '3px'):
                raise RuntimeError('requested custom properties did not reach new-tab document')
            selector_states={}
            menu_control=None
            if selector_query:
                selector_states['open']=selector_query(m,selector_records)
                menu_control=m.execute_script("""
                    const e=document.createElement('div'); e.className='context-menu';
                    e.setAttribute('data-textfox-context-probe','true');document.body.append(e);
                    const s=getComputedStyle(e);
                    return {border:s.borderTopWidth,radius:s.borderTopLeftRadius,shadow:s.boxShadow};
                    """)
                try:
                    selector_states['classControl']=selector_query(m,selector_records)
                    candidates=[r for r in selector_states['classControl'] if r['selector']=='.context-menu']
                    if len(candidates)!=1 or candidates[0]['status']!='structural-match':
                        raise RuntimeError(f'context menu selector control did not match: {candidates}')
                    if themed and (menu_control['border']!='7px' or menu_control['radius']!='3px'):
                        raise RuntimeError(f'context menu style control failed: {menu_control}')
                finally:
                    m.execute_script("document.querySelector('[data-textfox-context-probe]')?.remove()")
            m.find_element('tag name', 'body').send_keys(Keys.ARROW_DOWN)
            focus = m.execute_script("""
                const active = document.activeElement, inner = active?.shadowRoot?.activeElement;
                const style = inner && getComputedStyle(inner);
                return {active:active?.outerHTML.slice(0, 400), inner:inner?.outerHTML.slice(0, 400),
                  focusVisible:inner?.matches(':focus-visible'), outlineStyle:style?.outlineStyle,
                  outlineWidth:style?.outlineWidth};
                """)
            print(json.dumps({'firefox': metadata, 'themed': themed, 'result': result,
                              'focus': focus,'selectorStates':selector_states,'menuControl':menu_control}), flush=True)
            if (not focus.get('inner') or 'role="menuitem"' not in focus['inner']
                    or not focus.get('focusVisible') or focus.get('outlineStyle') == 'none'
                    or focus.get('outlineWidth') == '0px'):
                raise RuntimeError(f'shortcut menu item has no computed keyboard focus outline: {focus}')
            m.find_element('tag name', 'body').send_keys(Keys.ESCAPE)
            deadline = time.monotonic() + 10
            while m.execute_script("return !!document.querySelector('.top-site-outer panel-list:popover-open')"):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Escape did not close shortcut menu')
                time.sleep(.1)
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
                check(browser_nova, newtab_nova, themed)
