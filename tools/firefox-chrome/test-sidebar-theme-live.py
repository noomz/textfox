#!/usr/bin/env python3
"""Check configured sidebar launcher colors under temporary Firefox themes."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import zipfile

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(nova, dark):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-sidebar-theme-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        background = 'rgb(23, 47, 71)' if dark else 'rgb(232, 240, 248)'
        foreground = 'rgb(255, 255, 255)' if dark else 'rgb(0, 0, 0)'
        (profile / 'chrome' / 'config.css').write_text(f':root {{ --tf-bg: {background}; }}\n')
        theme_file = profile / 'probe-theme.xpi'
        manifest = {'manifest_version': 2, 'name': 'textfox probe theme', 'version': '1.0',
                    'browser_specific_settings': {'gecko': {'id': 'textfox-probe@example.invalid'}},
                    'theme': {'colors': {'frame': '#172f47' if dark else '#e8f0f8',
                                         'tab_background_text': '#ffffff' if dark else '#000000'}}}
        with zipfile.ZipFile(theme_file, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'sidebar.revamp': True, 'sidebar.verticalTabs': True,
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
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled')}", sandbox='system')
            if process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve() or meta['nova'] != nova:
                raise RuntimeError('unexpected Firefox process, profile, or Nova preference')
            enabled = m.execute_async_script("""
                const done = arguments[arguments.length - 1];
                const file = Cc['@mozilla.org/file/local;1'].createInstance(Ci.nsIFile);
                file.initWithPath(arguments[0]);
                AddonManager.installTemporaryAddon(file).then(a => done({id:a.id, active:a.isActive}), e => done({error:String(e)}));
                """, script_args=[str(theme_file)], sandbox='system')
            if enabled.get('id') != 'textfox-probe@example.invalid' or not enabled.get('active'):
                raise RuntimeError(f'probe theme did not enable: {enabled}')
            deadline = time.monotonic() + 20
            while not m.execute_script("return document.documentElement.hasAttribute('lwtheme') && !!document.querySelector('sidebar-main')?.shadowRoot?.querySelector('.buttons-wrapper')", sandbox='system'):
                if time.monotonic() >= deadline:
                    status = m.execute_script("return {root:document.documentElement.outerHTML.slice(0, 2000), sidebar:document.querySelector('sidebar-main')?.outerHTML.slice(0, 1000), shadow:!!document.querySelector('sidebar-main')?.shadowRoot, theme:Services.prefs.getStringPref('extensions.activeThemeID', '')}", sandbox='system')
                    raise RuntimeError(f'themed sidebar did not initialize: {status}')
                time.sleep(.2)
            result = m.execute_script("""
                const host = document.querySelector('sidebar-main'), hs = getComputedStyle(host);
                const wrapper = host.shadowRoot.querySelector('.buttons-wrapper'), ws = getComputedStyle(wrapper);
                return {rootTheme:document.documentElement.hasAttribute('lwtheme'),
                        hostId:host.id, hostBackground:hs.backgroundColor, hostImage:hs.backgroundImage,
                        hostColor:hs.color, wrapperBackground:ws.backgroundColor, wrapperColor:ws.color,
                        configuredBackground:hs.getPropertyValue('--tf-bg').trim()};
                """, sandbox='system')
            print(json.dumps({'firefox': meta, 'theme': enabled, 'dark': dark, 'result': result}), flush=True)
            hover = m.execute_async_script("""
                const done=arguments[arguments.length-1];
                SidebarController.toggleExpandOnHover(true).then(() => done(true), e => done(String(e)));
                """, sandbox='system')
            if hover is not True:
                raise RuntimeError(f'could not enable expand-on-hover: {hover}')
            hoverState = m.execute_script("""
                const c=document.getElementById('sidebar-container'), s=getComputedStyle(c);
                return {root:document.documentElement.hasAttribute('sidebar-expand-on-hover'),
                        color:s.color, background:s.backgroundColor, image:s.backgroundImage};
                """, sandbox='system')
            deadline = time.monotonic() + 10
            while m.execute_script("return document.getElementById('sidebar-container').hasAttribute('sidebar-launcher-expanded')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('sidebar was not initially collapsed')
                time.sleep(.1)
            container = m.find_element('id', 'sidebar-container')
            m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).pointer_move(0, 0, origin=container).perform()
            deadline = time.monotonic() + 10
            while not m.execute_script("return document.getElementById('sidebar-container').hasAttribute('sidebar-launcher-expanded')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('sidebar did not expand on pointer hover')
                time.sleep(.1)
            print(json.dumps({'nova':nova, 'dark':dark, 'expandOnHover':hoverState, 'expanded':True}), flush=True)
            if not hoverState['root'] or hoverState['color'] != foreground:
                raise RuntimeError('expand-on-hover theme state or foreground is incorrect')
            if result['configuredBackground'] != background or result['hostBackground'] != background:
                raise RuntimeError('themed sidebar launcher background does not follow --tf-bg')
            if result['hostColor'] != foreground or result['wrapperColor'] != foreground:
                raise RuntimeError('themed sidebar launcher foreground does not follow theme')
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
        for dark in (False, True):
            check(nova, dark)
