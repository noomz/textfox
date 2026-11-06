#!/usr/bin/env python3
"""Inspect URL-bar result backgrounds in disposable Firefox profiles."""
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


def check(nova, background):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-urlbar-colors-') as directory:
        profile = Path(directory)
        if background is not None:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
            (profile / 'chrome' / 'config.css').write_text(f':root {{ --tf-bg: {background}; }}\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'browser.urlbar.suggest.searches': False, 'browser.search.suggest.enabled': False,
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
            added = m.execute_async_script("""
                const done=arguments[arguments.length-1];
                Promise.all(['a','b'].map(s => PlacesUtils.bookmarks.insert({
                  parentGuid:PlacesUtils.bookmarks.unfiledGuid,
                  url:`https://example.invalid/textfoxunique${s}`, title:`textfoxunique ${s}`
                }))).then(() => done(true), error => done(String(error)));
                """, sandbox='system')
            if added is not True:
                raise RuntimeError(f'bookmark fixture failed: {added}')
            m.execute_script("gURLBar.focus(); gURLBar.search('textfoxunique')", sandbox='system')
            deadline = time.monotonic() + 20
            while not m.execute_script("return document.querySelectorAll('#urlbar[open] .urlbarView-row:not([hidden])').length >= 2", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('URL-bar result fixture did not initialize')
                time.sleep(.1)
            m.find_element('id', 'urlbar-input').send_keys(Keys.ARROW_DOWN)
            deadline = time.monotonic() + 10
            while not m.execute_script("return !!document.querySelector('#urlbar .urlbarView-row[selected]')?.textContent.includes('example.invalid')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('keyboard did not select a bookmark result')
                time.sleep(.1)
            result = m.execute_script("""
                const view=document.querySelector('#urlbar .urlbarView');
                const layer=view.querySelector('.urlbarView-background');
                const row=view.querySelector('.urlbarView-row[selected]');
                if (!layer || !row) throw new Error('result background or selected row missing');
                const root=getComputedStyle(document.documentElement), rs=getComputedStyle(row);
                return {viewBackground:getComputedStyle(view).backgroundColor,
                  layerBackground:getComputedStyle(layer).backgroundColor,
                  configuredBackground:root.getPropertyValue('--tf-bg').trim(),
                  oldToken:root.getPropertyValue('--urlbarView-highlight-background').trim(),
                  selectedToken:root.getPropertyValue('--urlbarview-background-color-selected').trim(),
                  rowBackground:rs.backgroundColor, rowColor:rs.color,
                  rowText:row.textContent.slice(0,120)};
                """, sandbox='system')
            if background is not None and result['configuredBackground'] != background:
                raise RuntimeError(f'configured background did not reach document: {result}')
            background_override = None
            if background is not None:
                background_override = m.execute_script("""
                    const view=document.querySelector('#urlbar .urlbarView');
                    const layer=view.querySelector('.urlbarView-background');
                    const row=view.querySelector('.urlbarView-row:not([selected]):not([hidden])');
                    if (!row) throw new Error('unselected result missing');
                    const beforeColor=getComputedStyle(row).color;
                    layer.style.setProperty('background-color', 'var(--tf-bg)', 'important');
                    const value=getComputedStyle(layer).backgroundColor;
                    const afterColor=getComputedStyle(row).color;
                    layer.style.removeProperty('background-color');
                    return {background:value, beforeColor, afterColor};
                    """, sandbox='system')
                if background_override['background'] != background:
                    raise RuntimeError(f'background intervention is inconclusive: {background_override}')
            intervention = m.execute_script("""
                const row=document.querySelector('#urlbar .urlbarView-row[selected]');
                const before=getComputedStyle(row).backgroundColor;
                row.style.setProperty('--urlbarView-highlight-background','rgb(240, 0, 240)','important');
                const oldValue=row.style.getPropertyValue('--urlbarView-highlight-background');
                const afterOld=getComputedStyle(row).backgroundColor;
                row.style.setProperty('--urlbarview-background-color-selected','rgb(0, 200, 100)','important');
                const newValue=row.style.getPropertyValue('--urlbarview-background-color-selected');
                const afterNew=getComputedStyle(row).backgroundColor;
                return {before, oldValue, afterOld, newValue, afterNew};
                """, sandbox='system')
            if (intervention['oldValue'] != 'rgb(240, 0, 240)'
                    or intervention['newValue'] != 'rgb(0, 200, 100)'
                    or intervention['afterNew'] != 'rgb(0, 200, 100)'):
                raise RuntimeError(f'selection token intervention is inconclusive: {intervention}')
            print(json.dumps({'firefox': metadata, 'themed': background is not None,
                              'result': result, 'backgroundOverride': background_override,
                              'selectionIntervention': intervention}), flush=True)
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
        for background in (None, 'rgb(23, 47, 71)', 'rgb(232, 240, 248)'):
            check(nova, background)
