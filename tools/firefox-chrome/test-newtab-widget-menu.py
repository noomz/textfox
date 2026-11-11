#!/usr/bin/env python3
"""Exercise widget menu keyboard activation in disposable profiles.

The new-tab feed may make network requests. Hiding widgets affects only the
throwaway profile. Run with the live-tool venv Python.
"""
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


def check(browser_nova, newtab_nova, themed, forced_colors, rtl, reduced_motion):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-widget-menu-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port': port, 'browser.nova.enabled': browser_nova,
                 'browser.newtabpage.activity-stream.nova.enabled': newtab_nova,
                 'browser.newtabpage.activity-stream.widgets.focusTimer.showSystemNotifications': True,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
                 'browser.newtabpage.activity-stream.widgets.enabled': True,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False,
                 'browser.display.document_color_use': 2 if forced_colors else 1,
                 'intl.l10n.pseudo': 'bidi' if rtl else '',
                 'ui.prefersReducedMotion': 1 if reduced_motion else 0}
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
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,browserNova:Services.prefs.getBoolPref('browser.nova.enabled'),newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled'),notifications:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.widgets.focusTimer.showSystemNotifications')}", sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['browserNova'] != browser_nova or meta['newtabNova'] != newtab_nova
                    or not meta['notifications']):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            selector = '.widgets-header-context-menu-button' if newtab_nova else '.focus-timer-context-menu-button'
            deadline = time.monotonic() + 30
            while not m.execute_script('return location.href === "about:newtab" && !!document.querySelector(arguments[0])', script_args=[selector]):
                if time.monotonic() >= deadline:
                    raise RuntimeError('widget menu did not initialize')
                time.sleep(.25)
            media = m.execute_script("return {forced:matchMedia('(forced-colors: active)').matches, reduced:matchMedia('(prefers-reduced-motion: reduce)').matches, direction:getComputedStyle(document.documentElement).direction}")
            if media['forced'] != forced_colors or media['reduced'] != reduced_motion or media['direction'] != ('rtl' if rtl else 'ltr'):
                raise RuntimeError(f'requested content forced-colors or direction not active: {media}')
            m.execute_script('document.querySelector(arguments[0]).focus()', script_args=[selector])
            m.find_element('css selector', selector).send_keys(Keys.ENTER)
            deadline = time.monotonic() + 10
            while not m.execute_script("return [...document.querySelectorAll('[role=menu]')].some(e => e.getClientRects().length)"):
                if time.monotonic() >= deadline:
                    raise RuntimeError('widget menu did not open')
                time.sleep(.1)
            m.find_element('tag name', 'body').send_keys(Keys.ARROW_DOWN)
            # Nova's popover menu moves focus asynchronously; classic is synchronous.
            expected = 'newtab-widget-section-menu-hide-all' if newtab_nova else 'newtab-widget-timer-menu-notifications'
            deadline = time.monotonic() + 5
            while True:
                state = m.execute_script("""
                const active = document.activeElement, inner=active?.shadowRoot?.activeElement;
                const s=inner && getComputedStyle(inner);
                const menu=[...document.querySelectorAll('[role=menu]')].find(e => e.getClientRects().length);
                const ms=menu && getComputedStyle(menu);
                return {active:active?.outerHTML.slice(0, 700),
                        shadowActive:inner?.outerHTML.slice(0, 700),
                        focusVisible:inner?.matches(':focus-visible'),
                        outlineStyle:s?.outlineStyle, outlineWidth:s?.outlineWidth,
                        menuBackground:ms?.backgroundColor, menuColor:ms?.color};
                """)
                print(json.dumps({'firefox': meta, 'themed': themed, 'focus': state}), flush=True)
                if (state['active'] and expected in state['active']
                        and state['shadowActive'] and 'role="menuitem"' in state['shadowActive']):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('expected first widget menu item is not focused')
                time.sleep(.25)
            if not state['focusVisible'] or state['outlineStyle'] == 'none' or state['outlineWidth'] == '0px':
                raise RuntimeError('widget menu item has no computed focus outline')
            m.find_element('tag name', 'body').send_keys(Keys.ENTER)
            deadline = time.monotonic() + 10
            if newtab_nova:
                while m.execute_script('return !!document.querySelector(arguments[0])', script_args=[selector]):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('activating Hide all did not remove widget menu')
                    time.sleep(.1)
            else:
                m.set_context('chrome')
                while m.execute_script("return Services.prefs.getBoolPref('browser.newtabpage.activity-stream.widgets.focusTimer.showSystemNotifications')", sandbox='system'):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('timer notification preference did not change')
                    time.sleep(.1)
            print(json.dumps({'browserNova': browser_nova, 'newtabNova': newtab_nova, 'themed': themed,
                              'forcedColors':forced_colors, 'rtl':rtl, 'reducedMotion':reduced_motion,
                              'menuActionObserved': True}), flush=True)
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
                for forced_colors in (False, True):
                    for rtl in (False, True):
                        for reduced_motion in (False, True):
                            check(browser_nova, newtab_nova, themed, forced_colors, rtl, reduced_motion)
