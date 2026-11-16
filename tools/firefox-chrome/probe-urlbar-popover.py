#!/usr/bin/env python3
"""Compare normal URL-bar popover state and height variables in disposable profiles."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(nova, themed):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-urlbar-popover-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port':port, 'browser.nova.enabled':nova,
                 'browser.search.suggest.enabled':False, 'browser.urlbar.suggest.searches':False,
                 'toolkit.legacyUserProfileCustomizations.stylesheets':True,
                 'browser.shell.checkDefaultBrowser':False}
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
            meta = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,
                  version:Services.appinfo.version,build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled')};
                """, sandbox='system')
            if process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve() or meta['nova'] != nova:
                raise RuntimeError(f'unexpected Firefox process, profile, or preferences: {meta}')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished && !!gURLBar.controller', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('URL bar startup did not finish')
                time.sleep(.1)
            measure = """
                const bar=gURLBar, anchor=bar.parentNode, s=getComputedStyle(anchor), r=anchor.getBoundingClientRect();
                return {tag:bar.localName, toolbar:bar.closest('toolbar')?.id, connected:bar.isConnected,
                  toolbarVisible:window.toolbar.visible, readOnly:bar.readOnly,
                  hostPopoverOpen:bar.matches(':popover-open'), hostPopoverAttribute:bar.hasAttribute('popover-open'),
                  panelPopoverOpen:bar.panel?.matches(':popover-open'), viewOpen:bar.view.isOpen,
                  anchorId:anchor.id, constructor:bar.constructor.name,
                  inlineHeight:anchor.style.getPropertyValue('--urlbar-container-height').trim(),
                  inlinePriority:anchor.style.getPropertyPriority('--urlbar-container-height'),
                  computedHeight:s.getPropertyValue('--urlbar-container-height').trim(),
                  toolbarHeight:s.getPropertyValue('--urlbar-toolbar-height').trim(),
                  measuredHeight:r.height};
                """
            before = m.execute_script(measure, sandbox='system')
            m.execute_script("gURLBar.focus(); gURLBar.search('mozilla')", sandbox='system')
            deadline = time.monotonic() + 10
            while True:
                after = m.execute_script(measure, sandbox='system')
                if after['viewOpen'] and after['panelPopoverOpen'] and after['hostPopoverAttribute']:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'URL bar results did not reach popover state: {before}, {after}')
                time.sleep(.1)
            if after['constructor'] != 'UrlbarInput' or after['hostPopoverOpen']:
                raise RuntimeError(f'unexpected regular URL bar fixture: {after}')
            if themed and (after['computedHeight'] != '32px' or after['toolbarHeight'] != '32px'):
                raise RuntimeError(f'textfox height assignments did not apply: {after}')
            print(json.dumps({'firefox':meta, 'themed':themed, 'before':before, 'open':after}), flush=True)
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
            check(nova, themed)
