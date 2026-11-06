#!/usr/bin/env python3
"""Inspect bookmarks toolbar alignment in disposable Firefox profiles."""
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


def check(nova, alignment):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-bookmarks-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(
            f':root {{ --tf-bookmarks-alignment: {alignment}; }}\n')
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
                return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path,
                  version:Services.appinfo.version, build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled')};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova):
                raise RuntimeError('unexpected Firefox process, profile, or Nova preference')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox startup did not finish')
                time.sleep(.1)
            inserted = m.execute_async_script("""
                const done = arguments[arguments.length - 1];
                setToolbarVisibility(document.getElementById('PersonalToolbar'), true);
                PlacesUtils.bookmarks.insert({parentGuid:PlacesUtils.bookmarks.toolbarGuid,
                  url:'https://example.com/', title:'Textfox bookmark fixture'})
                  .then(() => done(true), error => done(String(error)));
                """, sandbox='system')
            if inserted is not True:
                raise RuntimeError(f'could not insert bookmark: {inserted}')
            deadline = time.monotonic() + 20
            while not m.execute_script("return !!document.querySelector('#PlacesToolbarItems .bookmark-item[label=\"Textfox bookmark fixture\"]')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('bookmark toolbar item did not appear')
                time.sleep(.1)
            result = m.execute_script("""
                const toolbar = document.getElementById('PersonalToolbar');
                const container = document.getElementById('PlacesToolbarItems');
                const item = container.querySelector('.bookmark-item[label="Textfox bookmark fixture"]');
                const c = container.getBoundingClientRect(), i = item.getBoundingClientRect();
                return {option:getComputedStyle(document.documentElement).getPropertyValue('--tf-bookmarks-alignment').trim(),
                  display:getComputedStyle(container).display, alignment:getComputedStyle(container).justifyContent,
                  toolbarVisible:!toolbar.collapsed && getComputedStyle(toolbar).display !== 'none',
                  container:{left:c.left,right:c.right,width:c.width},
                  item:{left:i.left,right:i.right,width:i.width,height:i.height}};
                """, sandbox='system')
            print(json.dumps({'firefox': metadata, 'requested': alignment, 'result': result}), flush=True)
            if (result['option'] != alignment or result['alignment'] != alignment
                    or result['display'] != 'flex' or not result['toolbarVisible']
                    or result['item']['width'] <= 0 or result['item']['height'] <= 0):
                raise RuntimeError(f'bookmark toolbar configuration not applied: {result}')
            container, item = result['container'], result['item']
            if alignment == 'center':
                offset = abs((item['left'] + item['right']) - (container['left'] + container['right'])) / 2
            else:
                offset = abs(item['left'] - container['left'])
            if offset > 4:
                raise RuntimeError(f'bookmark is not positioned at requested alignment: {result}')
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
        for alignment in ('center', 'left'):
            check(nova, alignment)
