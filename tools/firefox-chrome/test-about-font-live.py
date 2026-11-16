#!/usr/bin/env python3
"""Check configured font on selected Firefox about pages in disposable profiles."""
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
PAGES = ('about:preferences', 'about:config', 'about:profiles', 'about:addons', 'about:support',
         'about:about', 'about:downloads', 'about:firefoxview', 'about:protections',
         'about:processes', 'about:memory', 'about:robots', 'about:logins')


def check(nova):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-about-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(':root { --tf-font-family: monospace; }\n')
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
            m.set_context('content')
            for page in PAGES:
                if page == 'about:logins':
                    m.set_context('chrome')
                    seeded = m.execute_async_script("""
                        const done=arguments[arguments.length-1];
                        const LoginInfo=new Components.Constructor('@mozilla.org/login-manager/loginInfo;1', Ci.nsILoginInfo, 'init');
                        const login=new LoginInfo('https://example.invalid','https://example.invalid',null,
                          'textfox-fixture-user','textfox-fixture-password','username','password');
                        Services.logins.addLoginAsync(login).then(()=>done(true),error=>done(String(error)));
                        """, sandbox='system')
                    if seeded is not True:
                        raise RuntimeError(f'disposable login fixture failed: {seeded}')
                    m.set_context('content')
                m.navigate(page)
                if page == 'about:logins':
                    deadline = time.monotonic() + 20
                    while not m.execute_script("return !!document.querySelector('login-list')?.shadowRoot?.querySelector('login-list-item[data-guid]')?.shadowRoot?.querySelector('.subtitle')"):
                        if time.monotonic() >= deadline:
                            raise RuntimeError('disposable login row did not initialize')
                        time.sleep(.1)
                if page == 'about:preferences':
                    deadline = time.monotonic() + 20
                    while not m.execute_script("return [...document.querySelectorAll('*')].some(e => e.localName === 'moz-page-nav-button')"):
                        if time.monotonic() >= deadline:
                            raise RuntimeError('about:preferences navigation controls did not initialize')
                        time.sleep(.1)
                script = """
                    const names = new Set(['button', 'input', 'select', 'textarea', 'checkbox', 'menulist', 'moz-button', 'moz-page-nav-button']);
                    if (location.href === 'about:about') names.add('a');
                    if (location.href === 'about:downloads') names.add('richlistbox');
                    if (location.href.split('#')[0] === 'about:logins') {
                      for (const name of ['login-list', 'login-item', 'login-intro', 'menu-button', 'fxaccounts-button']) names.add(name);
                    }
                    const controls = [...document.querySelectorAll('*')].filter(node => names.has(node.localName));
                    const shadowControls = controls.flatMap(host => host.shadowRoot
                      ? [...host.shadowRoot.querySelectorAll('*')].filter(node => names.has(node.localName))
                          .map(node => ({host:host.localName, tag:node.localName, font:getComputedStyle(node).fontFamily,
                            connected:node.isConnected, display:getComputedStyle(node).display,
                            rects:node.getClientRects().length, hostHidden:host.hidden}))
                      : []);
                    const fontTarget=document.body || document.documentElement;
                    const loginRow=location.href.split('#')[0] === 'about:logins'
                      ? document.querySelector('login-list')?.shadowRoot?.querySelector('login-list-item[data-guid]') : null;
                    const title=loginRow?.shadowRoot?.querySelector('.title'), subtitle=loginRow?.shadowRoot?.querySelector('.subtitle');
                    return {url:location.href, state:document.readyState,
                      populatedLogin:loginRow && title && subtitle ? {rowFont:getComputedStyle(loginRow).fontFamily,
                        titleFont:getComputedStyle(title).fontFamily,subtitleFont:getComputedStyle(subtitle).fontFamily,
                        fixtureUsername:subtitle.textContent.trim() === 'textfox-fixture-user'} : null,
                      listedAboutPages:location.href === 'about:about'
                        ? [...document.querySelectorAll('a[href^="about:"]')].map(a=>a.getAttribute('href')).sort() : null,
                      body:!!document.body, fontTarget:fontTarget?.localName,
                      rootId:document.documentElement.id,
                      downloadsList:location.href === 'about:downloads' ? !!document.getElementById('downloadsListBox') : null,
                      font:fontTarget && getComputedStyle(fontTarget).fontFamily,
                      controls:controls.length, shadowControlCount:shadowControls.length,
                      differingShadowControls:shadowControls.filter(node => node.font !== 'monospace'),
                      differingControls:controls.filter(node => getComputedStyle(node).fontFamily !== 'monospace')
                        .map(node => ({tag:node.localName, id:node.id, font:getComputedStyle(node).fontFamily,
                          connected:node.isConnected, display:getComputedStyle(node).display,
                          rects:node.getClientRects().length, hidden:node.hidden}))};
                    """
                deadline = time.monotonic() + 10
                while True:
                    result = m.execute_script(script)
                    differences = result['differingControls'] + result['differingShadowControls']
                    if not differences or any(item['font'] for item in differences):
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'about page controls did not reach a computed font: {result}')
                    time.sleep(.1)
                print(json.dumps({'firefox': metadata, 'requested': page, 'result': result}), flush=True)
                if (result['url'].split('#')[0] != page or not result['fontTarget'] or result['font'] != 'monospace'
                        or (page != 'about:downloads' and not result['body'])
                        or result['differingControls'] or result['differingShadowControls']):
                    raise RuntimeError(f'about page font or navigation differs from configuration: {result}')
                if result['controls'] == 0:
                    raise RuntimeError(f'{page} control fixture is empty')
                if page == 'about:logins' and (not result['populatedLogin'] or not result['populatedLogin']['fixtureUsername']
                        or any(result['populatedLogin'][name] != 'monospace' for name in ('rowFont', 'titleFont', 'subtitleFont'))):
                    raise RuntimeError(f'populated about logins fixture differs: {result}')
                if page == 'about:downloads' and (result['rootId'] != 'contentAreaDownloadsView' or not result['downloadsList']):
                    raise RuntimeError(f'about downloads fixture is incomplete: {result}')
                if page == 'about:about' and (not result['listedAboutPages'] or 'about:about' not in result['listedAboutPages']):
                    raise RuntimeError(f'about page index fixture is incomplete: {result}')
                if page in ('about:preferences', 'about:addons', 'about:logins') and result['shadowControlCount'] == 0:
                    raise RuntimeError(f'{page} shadow control fixture is empty')
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
        check(nova)
