#!/usr/bin/env python3
"""Check chrome and new-tab text contrast with Linux high-contrast settings.

Runs Firefox headed on a disposable headless Sway compositor, because the
headless LookAndFeel paints black on white and makes every ratio meaningless.

Firefox 159 facts this check is built on (tagged source 9a2dc53c):
- layout/style/PreferenceSheet.cpp: chrome documents "always use system
  colors, not stand-ins, not forced", so browser chrome never matches
  (forced-colors: active) on any platform. OS high contrast reaches chrome
  as (prefers-contrast: more).
- StaticPrefList.yaml: browser.display.document_color_use defaults to 1
  (never force) outside Windows, so on Linux content colors are forced only
  by the user's "Override colors: Always" setting (value 2).
- widget/gtk/nsLookAndFeel.cpp: UseAccessibilityTheme comes from a
  HighContrast GTK theme or the portal contrast setting. This check sets
  ui.useAccessibilityTheme, the override for that same value; the GTK
  detection step itself is not exercised (a HighContrast theme under
  headless Sway did not raise it).

Cases: high contrast off, or on together with "Override colors: Always";
browser Nova off and on; vanilla and textfox profiles. Every measured text
must reach the WCAG AA ratio of 4.5:1 against its nearest opaque background.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
MIN_RATIO = 4.5
MEASURE = r"""
const parse = c => { const x = document.createElement('canvas').getContext('2d'); x.fillStyle = '#000'; x.fillStyle = c;
  const v = x.fillStyle; if (v.startsWith('#')) return [parseInt(v.slice(1,3),16),parseInt(v.slice(3,5),16),parseInt(v.slice(5,7),16),1];
  const m = v.match(/[\d.]+/g).map(Number); return [m[0],m[1],m[2],m[3] ?? 1]; };
const lum = ([r,g,b]) => { const f = c => { c/=255; return c <= .03928 ? c/12.92 : ((c+.055)/1.055)**2.4; }; return .2126*f(r)+.7152*f(g)+.0722*f(b); };
const bgOf = el => { for (let e = el; e && e.nodeType === 1; e = e.parentElement || e.getRootNode().host) {
  const b = parse(getComputedStyle(e).backgroundColor); if (b[3] > 0.5) return {rgb:b, from:e.id || e.localName}; } return null; };
const out = {};
for (const [name, sel] of arguments[0]) { const el = document.querySelector(sel);
  if (!el) { out[name] = {missing:true}; continue; }
  const fg = parse(getComputedStyle(el).color), bg = bgOf(el);
  if (!bg) { out[name] = {fg:getComputedStyle(el).color, noBackground:true}; continue; }
  const [a,b] = [lum(fg), lum(bg.rgb)].sort((x,y)=>y-x);
  out[name] = {ratio:Math.round((a+.05)/(b+.05)*100)/100, fg:getComputedStyle(el).color, bg:bg.rgb, bgFrom:bg.from}; }
return out;"""
CHROME = [['urlbar', '#urlbar-input'], ['selectedTab', '.tabbrowser-tab[selected] .tab-label'],
          ['navbar', '#nav-bar'], ['bookmarks', '#PersonalToolbar']]
NEWTAB = [['tileTitle', '.top-site-outer .title'], ['body', 'body']]


def start_sway(root):
    (root / 'config').write_text('output * mode 1280x800\nseat seat0 fallback true\n')
    env = {'XDG_RUNTIME_DIR': str(root), 'WLR_BACKENDS': 'headless', 'WLR_RENDERER': 'pixman',
           'WLR_LIBINPUT_NO_DEVICES': '1', 'MOZ_ENABLE_WAYLAND': '1'}
    base = {k: v for k, v in os.environ.items() if k != 'MOZ_HEADLESS'}
    sway = subprocess.Popen(['sway', '--unsupported-gpu', '-c', str(root / 'config')], env={**base, **env},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 20
    while True:
        sockets = [p for p in root.glob('wayland-*') if p.is_socket()]
        if sockets:
            env['WAYLAND_DISPLAY'] = sockets[0].name
            return sway, {**base, **env}
        if sway.poll() is not None or time.monotonic() >= deadline:
            sway.terminate()
            raise RuntimeError('headless Sway did not start')
        time.sleep(.1)


def run(env, high_contrast, nova, themed):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-high-contrast-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
                 'browser.shell.checkDefaultBrowser': False}
        if high_contrast:
            prefs['ui.useAccessibilityTheme'] = 1
            prefs['browser.display.document_color_use'] = 2
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
            meta = m.execute_script(
                "return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, "
                "build:Services.appinfo.appBuildID, nova:Services.prefs.getBoolPref('browser.nova.enabled'), "
                "colorUse:Services.prefs.getIntPref('browser.display.document_color_use'), "
                "headless:Services.env.get('MOZ_HEADLESS'), wayland:Services.env.get('WAYLAND_DISPLAY')}",
                sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['nova'] != nova or meta['headless'] or not meta['wayland']):
                raise RuntimeError(f'unexpected Firefox process, profile, preference or display: {meta}')
            result = {'contrastMore': m.execute_script("return matchMedia('(prefers-contrast: more)').matches"),
                      'chromeForced': m.execute_script("return matchMedia('(forced-colors: active)').matches"),
                      'chrome': m.execute_script(MEASURE, script_args=[CHROME])}
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while not m.execute_script("return !!document.querySelector('.top-site-outer .title')"):
                if time.monotonic() >= deadline:
                    raise RuntimeError('new-tab shortcuts did not initialize')
                time.sleep(.25)
            result['contentForced'] = m.execute_script("return matchMedia('(forced-colors: active)').matches")
            result['newtab'] = m.execute_script(MEASURE, script_args=[NEWTAB])
            return meta, result
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
    failures = []
    with tempfile.TemporaryDirectory(prefix='tf-high-contrast-sway-') as directory:
        root = Path(directory)
        root.chmod(0o700)
        sway, env = start_sway(root)
        try:
            for high_contrast in (False, True):
                for nova in (False, True):
                    for themed in (False, True):
                        label = f'highContrast={high_contrast} nova={nova} themed={themed}'
                        try:
                            meta, result = run(env, high_contrast, nova, themed)
                        except Exception as error:
                            failures.append(f'{label}: {error}')
                            continue
                        print(json.dumps({'firefox': meta, 'highContrast': high_contrast, 'nova': nova,
                                          'themed': themed, 'result': result}), flush=True)
                        if result['contrastMore'] != high_contrast:
                            failures.append(f'{label}: prefers-contrast more is {result["contrastMore"]}')
                        if result['chromeForced']:
                            failures.append(f'{label}: browser chrome reports forced colors')
                        if result['contentForced'] != high_contrast:
                            failures.append(f'{label}: new-tab forced colors is {result["contentForced"]}')
                        for area in ('chrome', 'newtab'):
                            for name, sample in result[area].items():
                                ratio = sample.get('ratio')
                                if ratio is None:
                                    failures.append(f'{label}: {area} {name} not measurable: {sample}')
                                elif ratio < MIN_RATIO:
                                    failures.append(f'{label}: {area} {name} contrast {ratio} < {MIN_RATIO}')
        finally:
            sway.terminate()
            sway.wait(timeout=15)
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', file=sys.stderr)
        sys.exit(1)
    print('high-contrast text contrast holds for 8 cases')


if __name__ == '__main__':
    main()
