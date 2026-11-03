#!/usr/bin/env python3
"""Exercise Nova widget menu keyboard activation in disposable profiles.

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


def check(browser_nova, themed):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-widget-menu-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port': port, 'browser.nova.enabled': browser_nova,
                 'browser.newtabpage.activity-stream.nova.enabled': True,
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
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,browserNova:Services.prefs.getBoolPref('browser.nova.enabled'),newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled')}", sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['browserNova'] != browser_nova or not meta['newtabNova']):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while not m.execute_script('return location.href === "about:newtab" && !!document.querySelector(".nova-enabled .widgets-header-context-menu-button")'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Nova widget menu did not initialize')
                time.sleep(.25)
            button = m.find_element('css selector', '.widgets-header-context-menu-button')
            button.send_keys(Keys.ENTER)
            deadline = time.monotonic() + 10
            while not m.execute_script("return [...document.querySelectorAll('[role=menu]')].some(e => e.getClientRects().length)"):
                if time.monotonic() >= deadline:
                    raise RuntimeError('widget menu did not open')
                time.sleep(.1)
            m.find_element('tag name', 'body').send_keys(Keys.ARROW_DOWN)
            state = m.execute_script("""
                const active = document.activeElement;
                return {active:active?.outerHTML.slice(0, 700),
                        shadowActive:active?.shadowRoot?.activeElement?.outerHTML.slice(0, 700)};
                """)
            print(json.dumps({'firefox': meta, 'themed': themed, 'focus': state}), flush=True)
            if (not state['active'] or 'newtab-widget-section-menu-hide-all' not in state['active']
                    or not state['shadowActive'] or 'role="menuitem"' not in state['shadowActive']):
                raise RuntimeError('first widget menu item is not focused')
            m.find_element('tag name', 'body').send_keys(Keys.ENTER)
            deadline = time.monotonic() + 10
            while m.execute_script("return !!document.querySelector('.nova-enabled .widgets-header-context-menu-button')"):
                if time.monotonic() >= deadline:
                    raise RuntimeError('activating Hide all did not remove widget menu')
                time.sleep(.1)
            print(json.dumps({'browserNova': browser_nova, 'themed': themed,
                              'hideActionRemovedWidgetMenu': True}), flush=True)
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
        for themed in (False, True):
            check(browser_nova, themed)
