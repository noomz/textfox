#!/usr/bin/env python3
"""Check navigation and URL bar icon configuration in disposable Firefox profiles."""
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


def check(nova, display, rtl, high_contrast):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-navbar-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(
            f':root {{ --tf-display-nav-buttons: {display}; --tf-display-urlbar-icons: {display}; }}\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False,
                 'intl.l10n.pseudo': 'bidi' if rtl else '',
                 'ui.useAccessibilityTheme': 1 if high_contrast else 0}
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
            m.navigate('data:text/html,navbar-icon-fixture')
            m.set_context('chrome')
            deadline = time.monotonic() + 15
            while not m.execute_script("return document.querySelector('.urlbar-input-container')?.getAttribute('pageproxystate') === 'valid'", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('URL bar did not reach valid page state')
                time.sleep(.1)
            result = m.execute_script("""
                const root = getComputedStyle(document.documentElement);
                const buttons = ['back-button', 'forward-button'].map(id => {
                  const node = document.getElementById(id);
                  if (!node) throw new Error(`missing ${id}`);
                  const rect = node.getBoundingClientRect();
                  const style = getComputedStyle(node);
                  return {id, display:style.display, visibility:style.visibility,
                    width:rect.width, height:rect.height};
                });
                const star = document.getElementById('star-button-box');
                if (!star || star.hidden) throw new Error('bookmark star fixture is unavailable');
                const starRect = star.getBoundingClientRect(), starStyle = getComputedStyle(star);
                const urlbar = document.getElementById('urlbar');
                const urlbarStyle = getComputedStyle(urlbar), urlbarRect = urlbar.getBoundingClientRect();
                const heights = {toolbar:urlbarStyle.getPropertyValue('--urlbar-toolbar-height').trim(),
                  container:urlbarStyle.getPropertyValue('--urlbar-container-height').trim(),
                  urlbarHeight:urlbarRect.height};
                return {heights, direction:root.direction, prefersContrast:matchMedia('(prefers-contrast)').matches,
                  option:root.getPropertyValue('--tf-display-nav-buttons').trim(), buttons,
                  iconOption:root.getPropertyValue('--tf-display-urlbar-icons').trim(),
                  star:{display:starStyle.display, visibility:starStyle.visibility,
                    width:starRect.width, height:starRect.height}};
                """, sandbox='system')
            print(json.dumps({'firefox': metadata, 'requested': display, 'result': result}), flush=True)
            if (result['option'] != display or result['direction'] != ('rtl' if rtl else 'ltr')
                    or result['prefersContrast'] != high_contrast):
                raise RuntimeError('requested navigation button option, direction, or contrast not applied')
            for button in result['buttons']:
                if button['display'] != display:
                    raise RuntimeError(f"{button['id']} does not follow configuration: {button}")
                if display == 'flex' and (button['visibility'] != 'visible' or button['width'] <= 0 or button['height'] <= 0):
                    raise RuntimeError(f"{button['id']} is not visible: {button}")
                if display == 'none' and (button['width'] != 0 or button['height'] != 0):
                    raise RuntimeError(f"{button['id']} occupies space while hidden: {button}")
            star = result['star']
            if result['iconOption'] != display or star['display'] != display:
                raise RuntimeError(f'bookmark star does not follow URL bar icon configuration: {star}')
            if display == 'flex' and (star['visibility'] != 'visible' or star['width'] <= 0 or star['height'] <= 0):
                raise RuntimeError(f'configured bookmark star is not visible: {star}')
            if display == 'none' and (star['width'] != 0 or star['height'] != 0):
                raise RuntimeError(f'hidden bookmark star occupies space: {star}')
            if display == 'flex':
                first = 'data:text/html,first-navbar-test'
                second = 'data:text/html,second-navbar-test'
                m.set_context('content')
                m.navigate(first)
                m.navigate(second)
                m.set_context('chrome')
                for button, expected in (('back-button', first), ('forward-button', second)):
                    m.find_element('id', button).click()
                    deadline = time.monotonic() + 10
                    while m.execute_script('return gBrowser.currentURI.spec', sandbox='system') != expected:
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f'{button} did not navigate to {expected}')
                        time.sleep(.1)
                m.actions.sequence('key', 'keyboard').send_keys(Keys.F6).perform()
                for _ in range(30):
                    if m.execute_script("return document.activeElement?.id === 'sidebar-button'", sandbox='system'):
                        break
                    m.actions.sequence('key', 'keyboard').send_keys(Keys.TAB).perform()
                else:
                    raise RuntimeError('keyboard traversal did not reach sidebar toolbar button')
                m.actions.sequence('key', 'keyboard').send_keys(Keys.ARROW_LEFT if rtl else Keys.ARROW_RIGHT).perform()
                if not m.execute_script("return document.activeElement?.id === 'back-button'", sandbox='system'):
                    raise RuntimeError('toolbar arrow navigation did not focus Back')
                focus_style = m.execute_script("""
                    const button = document.activeElement;
                    const icon = button.querySelector('.toolbarbutton-icon');
                    if (!icon) throw new Error('Back button icon missing');
                    const style = getComputedStyle(icon);
                    return {focusVisible:button.matches(':focus-visible'), outlineStyle:style.outlineStyle,
                            outlineWidth:style.outlineWidth, boxShadow:style.boxShadow};
                    """, sandbox='system')
                print(json.dumps({'nova': nova, 'rtl': rtl, 'highContrast': high_contrast,
                                  'backFocusStyle': focus_style}), flush=True)
                if (not focus_style['focusVisible'] or focus_style['outlineStyle'] == 'none'
                        or focus_style['outlineWidth'] == '0px'):
                    raise RuntimeError(f'Back icon has no computed keyboard focus outline: {focus_style}')
                m.actions.sequence('key', 'keyboard').send_keys(Keys.ENTER).perform()
                deadline = time.monotonic() + 10
                while m.execute_script('return gBrowser.currentURI.spec', sandbox='system') != first:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('keyboard activation of Back did not navigate')
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
    for nova in (False, True):
        for display in ('none', 'flex'):
            for rtl in (False, True):
                for high_contrast in (False, True):
                    check(nova, display, rtl, high_contrast)
