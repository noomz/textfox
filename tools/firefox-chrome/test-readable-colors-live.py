#!/usr/bin/env python3
"""Check that chrome text stays readable when --tf-bg does not match the theme.

Runs Firefox headed on a disposable headless Sway compositor (see
test-high-contrast-live.py: the headless LookAndFeel paints black on white).

Fixtures: Firefox's built-in light and dark themes, each with the default
config and with a config.css whose --tf-bg is the opposite lightness, in both
browser Nova states. Every sample must reach 4.5:1 against its nearest opaque
background, walking slots and shadow hosts. A last fixture sets
--tf-fg: var(--lwt-text-color) and checks that the theme's own text color
comes back, so the variable is a real override.

Bound: computed colors on a real compositor, not painted pixels; one platform.
"""
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
start_sway = runpy.run_path(str(HERE / 'test-high-contrast-live.py'))['start_sway']
MIN_RATIO = 4.5
PROBE_THEME_ID = 'textfox-readable-probe@example.invalid'
# Built-in themes (no --lwt-* on Firefox 159) and static themes with tinted text.
THEMES = {
    'builtin-light': {'id': 'firefox-compact-light@mozilla.org', 'dark': False},
    'builtin-dark': {'id': 'firefox-compact-dark@mozilla.org', 'dark': True},
    'static-light': {'id': PROBE_THEME_ID, 'dark': False, 'frame': '#eff1f5', 'text': '#4c4f69'},
    'static-dark': {'id': PROBE_THEME_ID, 'dark': True, 'frame': '#1e1e2e', 'text': '#b4befe'},
}
OPPOSITE_BG = {False: 'rgb(23, 47, 71)', True: 'rgb(232, 240, 248)'}

MEASURE = r"""
const parse = c => { const x = document.createElement('canvas').getContext('2d'); x.fillStyle = '#000'; x.fillStyle = c;
  const v = x.fillStyle; if (v.startsWith('#')) return [parseInt(v.slice(1,3),16),parseInt(v.slice(3,5),16),parseInt(v.slice(5,7),16),1];
  const m = v.match(/[\d.]+/g).map(Number); return [m[0],m[1],m[2],m[3] ?? 1]; };
const lum = ([r,g,b]) => { const f = c => { c/=255; return c <= .03928 ? c/12.92 : ((c+.055)/1.055)**2.4; }; return .2126*f(r)+.7152*f(g)+.0722*f(b); };
const up = e => e.assignedSlot || e.parentElement || (e.getRootNode() !== document && e.getRootNode().host) || null;
const bgOf = (el, pseudo, bgSel) => {
  if (bgSel) { const e = find(bgSel); if (!e) return null; const b = parse(getComputedStyle(e).backgroundColor);
    return b[3] > 0.5 ? {rgb:b, from:bgSel} : null; }
  if (pseudo) { const b = parse(getComputedStyle(el, pseudo).backgroundColor); if (b[3] > 0.5) return {rgb:b, from:pseudo}; }
  for (let e = el; e && e.nodeType === 1; e = up(e)) {
    const b = parse(getComputedStyle(e).backgroundColor); if (b[3] > 0.5) return {rgb:b, from:e.id || e.localName}; }
  return null; };
const find = sel => { let el = document;
  for (const part of sel.split(' >>> ')) { el = (el.shadowRoot || el).querySelector(part); if (!el) return null; } return el; };
const out = {};
for (const [name, sel, pseudo, bgSel] of arguments[0]) { const el = find(sel);
  if (!el) { out[name] = {missing:true}; continue; }
  const style = getComputedStyle(el, pseudo || null);
  if (pseudo && (style.content === 'none' || style.display === 'none')) { out[name] = {hidden:true}; continue; }
  const fg = parse(style.color), bg = bgOf(el, pseudo, bgSel);
  if (!bg) { out[name] = {fg:style.color, noBackground:true}; continue; }
  const [a,b] = [lum(fg), lum(bg.rgb)].sort((x,y)=>y-x);
  out[name] = {ratio:Math.round((a+.05)/(b+.05)*100)/100, fg:style.color, bg:bg.rgb, bgFrom:bg.from}; }
return out;"""

CHROME = [['navbar', '#nav-bar'], ['urlbar', '#urlbar-input'],
          ['selectedTab', '.tabbrowser-tab[selected] .tab-label', None, '.tabbrowser-tab[selected] .tab-background'],
          ['otherTab', '.tabbrowser-tab:not([selected]) .tab-label'],
          ['mainTitle', '#tabbrowser-tabbox', '::before'],
          ['navbarTitle', '#nav-bar', '::before'],
          ['tabsTitle', '#TabsToolbar', '::before'],
          ['findInput', 'findbar:not([hidden]) .findbar-textbox'],
          ['findCheckbox', 'findbar:not([hidden]) .findbar-highlight .checkbox-label'],
          ['findTitle', 'findbar:not([hidden])', '::before']]
URLBAR_VIEW = [['urlbarResult', '.urlbarView-row .urlbarView-title']]
APP_MENU = [['appMenuItem', '#appMenu-new-tab-button2'], ['appMenuSettings', '#appMenu-settings-button']]
CONTEXT_MENU = [['contextItem', '#toolbar-context-menu menuitem:not([hidden])']]


def wait(m, script, what, timeout=20):
    deadline = time.monotonic() + timeout
    while not m.execute_script(script, sandbox='system'):
        if time.monotonic() >= deadline:
            raise RuntimeError(f'{what} did not happen')
        time.sleep(.2)


def run(env, theme, nova, config):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-readable-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        # Horizontal tabs are hidden by default; show them so tab text can be measured.
        (profile / 'chrome' / 'config.css').write_text(
            f':root {{ --tf-display-horizontal-tabs: block; {config} }}\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env={**env, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            spec = THEMES[theme]
            if 'frame' in spec:
                theme_file = profile / 'probe-theme.xpi'
                manifest = {'manifest_version': 2, 'name': 'textfox readable probe', 'version': '1.0',
                            'browser_specific_settings': {'gecko': {'id': PROBE_THEME_ID}},
                            'theme': {'colors': {'frame': spec['frame'], 'tab_background_text': spec['text']}}}
                with zipfile.ZipFile(theme_file, 'w') as archive:
                    archive.writestr('manifest.json', json.dumps(manifest))
                enable = ("const f = Cc['@mozilla.org/file/local;1'].createInstance(Ci.nsIFile); "
                          "f.initWithPath(arguments[0]); return AddonManager.installTemporaryAddon(f)")
                argument = str(theme_file)
            else:
                enable = "return AddonManager.getAddonByID(arguments[0]).then(a => a.enable())"
                argument = spec['id']
            m.execute_async_script(
                f"const done = arguments[arguments.length - 1]; "
                f"(async () => {{ {enable}; }})().then(() => done(), e => done(String(e)))",
                script_args=[argument], sandbox='system')
            wait(m, f"return Services.prefs.getStringPref('extensions.activeThemeID', '') == {json.dumps(spec['id'])}"
                    + (" && document.documentElement.hasAttribute('lwtheme')" if 'frame' in spec else ''),
                 f'{theme} theme activation', 30)
            meta = m.execute_script(
                "return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, "
                "build:Services.appinfo.appBuildID, nova:Services.prefs.getBoolPref('browser.nova.enabled'), "
                "headless:Services.env.get('MOZ_HEADLESS'), wayland:Services.env.get('WAYLAND_DISPLAY'), "
                "themeText:getComputedStyle(document.documentElement).getPropertyValue('--lwt-text-color').trim(), "
                "tfFg:getComputedStyle(document.documentElement).getPropertyValue('--tf-fg').trim()}",
                sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['nova'] != nova or meta['headless'] or not meta['wayland']):
                raise RuntimeError(f'unexpected Firefox process, profile, preference or display: {meta}')
            m.execute_script("gBrowser.addTrustedTab('about:blank'); gBrowser.getFindBar().then(f => f.open())",
                             sandbox='system')
            wait(m, "return gBrowser.tabs.length == 2 && !!document.querySelector('findbar:not([hidden])')",
                 'findbar open')
            samples = m.execute_script(MEASURE, script_args=[CHROME], sandbox='system')
            m.execute_script("gURLBar.focus(); gURLBar.search('about:')", sandbox='system')
            wait(m, "return gURLBar.view.isOpen && !!document.querySelector('.urlbarView-row .urlbarView-title')",
                 'URL bar results')
            samples.update(m.execute_script(MEASURE, script_args=[URLBAR_VIEW], sandbox='system'))
            m.execute_script("gURLBar.view.close(); PanelUI.show()", sandbox='system')
            wait(m, "return document.getElementById('appMenu-popup').state == 'open'", 'app menu open')
            samples.update(m.execute_script(MEASURE, script_args=[APP_MENU], sandbox='system'))
            m.execute_script("PanelUI.hide(); document.getElementById('toolbar-context-menu')"
                             ".openPopup(document.getElementById('nav-bar'), 'after_start')", sandbox='system')
            wait(m, "return document.getElementById('toolbar-context-menu').state == 'open'", 'context menu open')
            samples.update(m.execute_script(MEASURE, script_args=[CONTEXT_MENU], sandbox='system'))
            return meta, samples
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


def main():
    cases = []
    for nova in (False, True):
        for theme, spec in THEMES.items():
            cases.append((theme, nova, '', 'default'))
            cases.append((theme, nova, f'--tf-bg: {OPPOSITE_BG[spec["dark"]]};', 'opposite-bg'))
    cases.append(('static-dark', False, '--tf-fg: var(--lwt-text-color);', 'theme-fg'))
    failures = []
    with tempfile.TemporaryDirectory(prefix='tf-readable-sway-') as directory:
        root = Path(directory)
        root.chmod(0o700)
        sway, env = start_sway(root)
        try:
            for theme, nova, config, kind in cases:
                label = f'theme={theme} nova={nova} config={kind}'
                try:
                    meta, samples = run(env, theme, nova, config)
                except Exception as error:
                    failures.append(f'{label}: {error}')
                    continue
                print(json.dumps({'firefox': meta, 'theme': theme, 'nova': nova, 'config': config,
                                  'samples': samples}), flush=True)
                if kind == 'theme-fg':
                    if meta['tfFg'] != meta['themeText']:
                        failures.append(f'{label}: --tf-fg {meta["tfFg"]!r} is not the theme text '
                                        f'{meta["themeText"]!r}')
                    probes = ('navbar', 'appMenuItem', 'contextItem')
                    seen = {name: samples.get(name, {}).get('fg') for name in probes}
                    if len(set(seen.values())) != 1 or seen['navbar'] is None:
                        failures.append(f'{label}: --tf-fg override did not reach all text: {seen}')
                    continue
                for name, sample in samples.items():
                    if sample.get('hidden'):
                        continue
                    ratio = sample.get('ratio')
                    if ratio is None:
                        failures.append(f'{label}: {name} not measurable: {sample}')
                    elif ratio < MIN_RATIO:
                        failures.append(f'{label}: {name} contrast {ratio} < {MIN_RATIO} '
                                        f'({sample["fg"]} on {sample["bg"]} from {sample["bgFrom"]})')
        finally:
            sway.terminate()
            sway.wait(timeout=15)
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', file=sys.stderr)
        sys.exit(1)
    print(f'text stays readable in {len(cases)} cases')


if __name__ == '__main__':
    main()
