#!/usr/bin/env python3
"""Inspect app-menu colors before and after a temporary background override."""
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


def check(nova, color_preference):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-panel-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(
            ':root { --tf-bg: rgb(23, 47, 71); --tf-accent: rgb(19, 83, 137); '
            '--tf-border-width: 5px; --tf-rounding: 7px; }\n')
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
            metadata = m.execute_script("return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, build:Services.appinfo.appBuildID, nova:Services.prefs.getBoolPref('browser.nova.enabled'), documentColorUse:Services.prefs.getIntPref('browser.display.document_color_use')}", sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova or metadata['documentColorUse'] != color_preference):
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
                const s = getComputedStyle(slot);
                const root = getComputedStyle(document.documentElement);
                const properties = ['--lwt-text-color', '--toolbar-color', '--panel-text-color', '--tf-bg'];
                return {background:s.backgroundColor, color:s.color, border:s.border,
                        radius:s.borderRadius, forcedColors:matchMedia('(forced-colors: active)').matches,
                        rootProperties:Object.fromEntries(properties.map(p => [p, root.getPropertyValue(p).trim()]))};
                """, sandbox='system')
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
            print(json.dumps({'firefox': metadata, 'baseline': result, 'override': override,
                              'keyboardFocus': focused, 'afterEscape': closed}), flush=True)
            if focused['panelState'] != 'open' or not focused['inPanel'] or closed != 'closed':
                raise RuntimeError('app menu keyboard navigation or Escape failed')
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
            check(nova, color_preference)
