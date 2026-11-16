#!/usr/bin/env python3
"""Inspect search handoff controls in disposable Firefox profiles.

The disposable new-tab profile enables feeds and may make network requests.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from marionette_driver.errors import NoSuchElementException, StaleElementException

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(browser_nova, newtab_nova, themed):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-search-handoff-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
            (profile / 'chrome' / 'config.css').write_text(
                ':root { --tf-border-width: 7px; --tf-rounding: 3px; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': browser_nova,
                 'browser.newtabpage.activity-stream.nova.enabled': newtab_nova,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
                 'browser.newtabpage.activity-stream.widgets.enabled': True,
                 'browser.newtabpage.activity-stream.improvesearch.handoffToAwesomebar': True,
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
                  newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled'),
                  handoff:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.improvesearch.handoffToAwesomebar')};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['browserNova'] != browser_nova or metadata['newtabNova'] != newtab_nova
                    or not metadata['handoff']):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            selector = '.search-wrapper content-search-handoff-ui'
            deadline = time.monotonic() + 30
            while not m.execute_script("""
                const h=document.querySelector(arguments[0]);
                const b=h?.shadowRoot?.querySelector('.search-handoff-button');
                return location.href === 'about:newtab' && !!b && b.getBoundingClientRect().width > 0 && !h.hasAttribute('disabled') && !!document.querySelector('.top-site-outer .tile');
                """, script_args=[selector]):
                if time.monotonic() >= deadline:
                    raise RuntimeError('search handoff fixture did not initialize')
                time.sleep(.2)
            measurements = None
            deadline = time.monotonic() + 30
            while measurements is None:
                measurements = m.execute_script("""
                const wrapper=document.querySelector('.search-wrapper');
                const host=wrapper?.querySelector('content-search-handoff-ui');
                const button=host?.shadowRoot?.querySelector('.search-handoff-button');
                const tileElement=document.querySelector('.top-site-outer .tile');
                if (!button || !tileElement) return null;
                const root=getComputedStyle(document.documentElement);
                const tile=getComputedStyle(tileElement);
                function sample() {
                  const s=getComputedStyle(button), rect=button.getBoundingClientRect();
                  return {lightDomButton:!!wrapper.querySelector('.search-handoff-button'),
                    lightDomInput:!!wrapper.querySelector('input'), shadowButton:!!button,
                    nonhandoff:host.getAttribute('nonhandoff'),
                    width:rect.width, height:rect.height, border:s.borderTopWidth,
                    borderStyle:s.borderTopStyle, borderColor:s.borderTopColor, radius:s.borderTopLeftRadius,
                    background:s.backgroundColor, shadow:s.boxShadow,
                    tfWidth:root.getPropertyValue('--tf-border-width').trim(),
                    tfRounding:root.getPropertyValue('--tf-rounding').trim(),
                    tileBorder:tile.borderTopWidth, tileRadius:tile.borderTopLeftRadius};
                }
                const baseline=sample();
                host.style.setProperty('--content-search-handoff-ui-border-width', '7px');
                host.style.setProperty('--content-search-handoff-ui-border-radius', '3px');
                const control=sample();
                host.style.removeProperty('--content-search-handoff-ui-border-width');
                host.style.removeProperty('--content-search-handoff-ui-border-radius');
                return {baseline, control};
                """)
                if measurements is None:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('search handoff disappeared during measurement')
                    time.sleep(.1)
            baseline = measurements['baseline']
            control = measurements['control']
            if baseline['width'] <= 0 or baseline['height'] <= 0 or baseline['nonhandoff'] != 'false':
                raise RuntimeError('not a visible handoff fixture')
            if themed and (baseline['tfWidth'] != '7px' or baseline['tfRounding'] != '3px'
                           or baseline['tileBorder'] != '7px' or baseline['tileRadius'] != '3px'):
                raise RuntimeError('configured properties did not reach the shortcut control')
            if control['border'] != '7px' or control['radius'] != '3px':
                raise RuntimeError('host variable control did not affect shadow button')
            m.execute_script("document.body.tabIndex = -1; document.body.focus();")
            m.set_context('chrome')
            if m.execute_script('return gURLBar.focused', sandbox='system'):
                raise RuntimeError('URL bar was already focused before handoff click')
            m.set_context('content')
            deadline = time.monotonic() + 10
            stale_retries = 0
            missing_retries = 0
            while True:
                try:
                    m.find_element('css selector', selector).click()
                    break
                except NoSuchElementException:
                    missing_retries += 1
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.1)
                except StaleElementException:
                    stale_retries += 1
                    if time.monotonic() >= deadline:
                        raise
            m.set_context('chrome')
            deadline = time.monotonic() + 10
            while not m.execute_script('return gURLBar.focused', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('handoff click did not focus URL bar')
                time.sleep(.1)
            print(json.dumps({'firefox': metadata, 'themed': themed, 'baseline': baseline,
                              'control': control, 'handoffFocusedUrlbar': True,
                              'staleClickRetries': stale_retries,
                              'missingClickRetries': missing_retries}), flush=True)
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
