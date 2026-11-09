#!/usr/bin/env python3
"""Inspect horizontal tab colors and current Firefox theme tokens."""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import zipfile

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def contrast(background, foreground):
    match = re.fullmatch(r'color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)\)', background)
    fg = re.fullmatch(r'rgb\((\d+), (\d+), (\d+)\)', foreground)
    if not match or not fg:
        return None
    def luminance(channels):
        def linear(c):
            return c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
        return sum(w * linear(c) for w, c in zip((.2126, .7152, .0722), channels))
    a = luminance(map(float, match.groups()))
    b = luminance(int(c) / 255 for c in fg.groups())
    return (max(a, b) + .05) / (min(a, b) + .05)


def check(nova, dark, source, forced, mismatch):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-tab-colors-') as directory:
        profile = Path(directory)
        shutil.copytree(source / 'chrome', profile / 'chrome')
        bg = 'rgb(23, 47, 71)' if dark != mismatch else 'rgb(232, 240, 248)'
        (profile / 'chrome' / 'config.css').write_text(f':root {{ --tf-bg: {bg}; --tf-display-horizontal-tabs: block; }}\n')
        theme = profile / 'probe-theme.xpi'
        manifest = {'manifest_version': 2, 'name': 'textfox tab probe', 'version': '1.0',
                    'browser_specific_settings': {'gecko': {'id': 'textfox-tab-probe@example.invalid'}},
                    'theme': {'colors': {'frame': '#172f47' if dark else '#e8f0f8',
                                         'tab_background_text': '#ffffff' if dark else '#000000'}}}
        with zipfile.ZipFile(theme, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova, 'sidebar.verticalTabs': False,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False,
                 'ui.useAccessibilityTheme': 1 if forced else 0}
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
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled'),vertical:Services.prefs.getBoolPref('sidebar.verticalTabs')}", sandbox='system')
            if process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve() or meta['nova'] != nova or meta['vertical']:
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            enabled = m.execute_async_script("""
                const done = arguments[arguments.length - 1];
                const file = Cc['@mozilla.org/file/local;1'].createInstance(Ci.nsIFile);
                file.initWithPath(arguments[0]);
                AddonManager.installTemporaryAddon(file).then(a => done({id:a.id, active:a.isActive}), e => done({error:String(e)}));
                """, script_args=[str(theme)], sandbox='system')
            if enabled.get('id') != 'textfox-tab-probe@example.invalid' or not enabled.get('active'):
                raise RuntimeError(f'temporary theme failed: {enabled}')
            deadline = time.monotonic() + 20
            while not m.execute_script("return document.documentElement.hasAttribute('lwtheme') && !!document.querySelector('#TabsToolbar .tabbrowser-tab[selected] .tab-background')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('themed horizontal tab did not initialize')
                time.sleep(.1)
            js = """
                const tab = document.querySelector('#TabsToolbar .tabbrowser-tab[selected]');
                const bg = getComputedStyle(tab.querySelector('.tab-background'));
                const text = getComputedStyle(tab.querySelector('.tab-content'));
                const root = getComputedStyle(document.documentElement);
                const names = ['--tf-bg', '--toolbar-field-color', '--toolbar-bgcolor',
                    '--tab-selected-bgcolor', '--tab-hover-background-color',
                    '--tab-background-color-selected', '--tab-background-color-hover',
                    '--tab-text-color-selected'];
                return {background:bg.backgroundColor, color:text.color,
                        prefersContrast:matchMedia('(prefers-contrast)').matches,
                        forcedColors:matchMedia('(forced-colors: active)').matches,
                        targetSelected:bg.getPropertyValue('--tab-selected-bgcolor').trim(),
                        props:Object.fromEntries(names.map(n => [n, root.getPropertyValue(n).trim()]))};
                """
            before = m.execute_script(js, sandbox='system')
            hover = m.execute_script("""
                const tab=gBrowser.addTrustedTab('about:blank');
                InspectorUtils.addPseudoClassLock(tab, ':hover');
                const bg=getComputedStyle(tab.querySelector('.tab-background'));
                const result={background:bg.backgroundColor, color:getComputedStyle(tab.querySelector('.tab-content')).color,
                              hover:tab.matches(':hover')};
                InspectorUtils.removePseudoClassLock(tab, ':hover');
                return result;
                """, sandbox='system')
            m.execute_script("""
                const tab=gBrowser.addTrustedTab('about:blank');
                tab.setAttribute('data-textfox-multi-probe','true');
                gBrowser.addToMultiSelectedTabs(tab);
                """, sandbox='system')
            deadline = time.monotonic() + 10
            while True:
                multi = m.execute_script("""
                    const tab=document.querySelector('.tabbrowser-tab[data-textfox-multi-probe]');
                    const bg=tab?.querySelector('.tab-background');
                    if (!tab || !bg) return null;
                    const s=getComputedStyle(bg), r=bg.getBoundingClientRect();
                    return {selected:tab.selected, multiselected:tab.multiselected,
                      tabAttr:tab.hasAttribute('multiselected'), bgAttr:bg.hasAttribute('multiselected'),
                      background:s.backgroundColor, width:r.width,height:r.height};
                    """, sandbox='system')
                if multi and multi['multiselected'] and multi['bgAttr'] and multi['width'] > 0 and multi['height'] > 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'multiselected tab fixture did not initialize: {multi}')
                time.sleep(.1)
            if multi['selected']:
                raise RuntimeError(f'multiselected fixture unexpectedly selected: {multi}')
            multi_intervention = m.execute_script("""
                const bg=document.querySelector('.tabbrowser-tab[data-textfox-multi-probe] .tab-background');
                bg.style.setProperty('--tab-selected-bgcolor','rgb(19, 83, 137)','important');
                const result=getComputedStyle(bg).backgroundColor;
                bg.style.removeProperty('--tab-selected-bgcolor');
                return result;
                """, sandbox='system')
            if multi_intervention != 'rgb(19, 83, 137)':
                raise RuntimeError(f'multiselected background intervention failed: {multi_intervention}')
            m.execute_script("""
                const bg = document.querySelector('#TabsToolbar .tabbrowser-tab[selected] .tab-background');
                bg.style.setProperty('--tab-selected-bgcolor', 'var(--tab-background-color-selected)', 'important');
                """, sandbox='system')
            after = m.execute_script(js, sandbox='system')
            if before['prefersContrast'] != forced:
                raise RuntimeError('requested contrast preference state not active')
            print(json.dumps({'firefox': meta, 'dark': dark, 'forced':forced, 'mismatch':mismatch, 'before': before, 'hover':hover, 'after': after,
                              'multi':multi, 'multiIntervention':multi_intervention,
                              'computedSelectedContrast':contrast(before['background'], before['color']),
                              'computedHoverContrast':contrast(hover['background'], hover['color'])}), flush=True)
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--high-contrast', action='store_true')
    parser.add_argument('--mismatch', action='store_true')
    args = parser.parse_args()
    for nova in (False, True):
        for dark in (False, True):
            check(nova, dark, args.source, args.high_contrast, args.mismatch)
