#!/usr/bin/env python3
"""Does hovering a background tab open Firefox's tab preview panel (issue #2)?

Headed (Sway on Linux), pointer hover through Marionette. Per case: a fresh profile, or a copy
of --profile; chrome/ from the repo (textfox), --chrome DIR, or none (vanilla). Reports whether
#tab-preview-panel opened, and, if not, each activation input Firefox checks
(tab-hover-preview.mjs: active window, no other open panel or menupopup, pref, delay), and
whether the open panel paints (its box differs from the capture after it closes).

Usage: probe-tab-preview.py [--profile DIR] [--chrome repo|none|DIR ...] [--nova false|true ...]
                            [--layout horizontal|vertical ...] [--theme SPEC] [--config CSS]
Exit 1 if any case fails to open or paint the panel.
"""
import argparse
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
import ff_themes
from paint_display import open_display, read_ppm

ROOT = Path(__file__).resolve().parents[2]
SKIP = {'lock', '.parentlock', 'parent.lock', 'cache2', 'startupCache', 'crashes', 'minidumps',
        'sessionstore-backups', 'sessionstore.jsonlz4', 'datareporting', 'saved-telemetry-pings'}

STATE_JS = r"""
const panel = document.getElementById('tab-preview-panel');
const open = [...document.querySelectorAll(
  'panel[panelopen=true]:not(#tab-preview-panel):not(#tabgroup-preview-panel):not(#tab-note-preview-panel),'
  + 'panel[animating=true]:not(#tab-preview-panel):not(#tabgroup-preview-panel):not(#tab-note-preview-panel),'
  + 'menupopup[open=true]')].map(e => e.id || e.className || e.nodeName);
const tab = gBrowser.tabs[1];
const r = panel ? panel.getBoundingClientRect() : null;
const cs = panel ? getComputedStyle(panel) : null;
return {
  state: panel ? panel.state : 'missing',
  rect: r && r.toJSON(), display: cs && cs.display, visibility: cs && cs.visibility, opacity: cs && cs.opacity,
  activeWindow: Services.focus.activeWindow === window,
  openPopups: open,
  tabHover: tab._hover === true, tabMatchesHover: tab.matches(':hover'),
  previewPanelLoaded: !!gBrowser.tabContainer.previewPanel,
  movingtab: gBrowser.tabContainer.hasAttribute('movingtab'),
  prefs: {enabled: Services.prefs.getBoolPref('browser.tabs.hoverPreview.enabled', null),
          thumbnails: Services.prefs.getBoolPref('browser.tabs.hoverPreview.showThumbnails', null),
          delay: Services.prefs.getIntPref('ui.tooltip.delay_ms', 500),
          autohide: Services.prefs.getBoolPref('ui.popup.disable_autohide', false),
          vertical: Services.prefs.getBoolPref('sidebar.verticalTabs', false),
          nova: Services.prefs.getBoolPref('browser.nova.enabled', false)},
  tabRect: tab.getBoundingClientRect().toJSON(),
  tabVisible: tab.visible, selected: gBrowser.selectedTab === gBrowser.tabs[0], tabs: gBrowser.tabs.length,
  tabDisplay: getComputedStyle(tab).display,
  strip: ['TabsToolbar', 'tabbrowser-tabs', 'vertical-tabs', 'sidebar-main'].map(id => {
    const e = document.getElementById(id);
    if (!e) return [id, null];
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return [id, [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
            s.display, s.visibility, e.hidden || e.collapsed || null];
  }),
};
"""


def prefs_js(prefs):
    return ''.join(f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items())


def run_case(display, source, chrome, nova, layout, theme, config, shots):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-preview-') as name:
        profile = Path(name) / 'profile'
        if source:
            shutil.copytree(source, profile, ignore=lambda d, names: [n for n in names if n in SKIP])
        else:
            profile.mkdir()
        if chrome != 'keep':
            shutil.rmtree(profile / 'chrome', ignore_errors=True)
            if chrome != 'none':
                shutil.copytree(ROOT / 'chrome' if chrome == 'repo' else Path(chrome), profile / 'chrome')
                # textfox hides the horizontal strip by default; a user wanting it sets this.
                shown = ':root { --tf-display-horizontal-tabs: block; }\n' if layout == 'horizontal' else ''
                if config is not None or shown:
                    (profile / 'chrome' / 'config.css').write_text(shown + (config or ''))
        prefs = {'marionette.port': port, 'browser.shell.checkDefaultBrowser': False,
                 'browser.startup.page': 0, 'toolkit.legacyUserProfileCustomizations.stylesheets': True}
        if nova is not None:
            prefs['browser.nova.enabled'] = nova
        if layout:
            prefs.update({'sidebar.revamp': layout == 'vertical', 'sidebar.verticalTabs': layout == 'vertical'})
        if theme:
            prefs.update(ff_themes.prefs(theme))
        user = profile / 'user.js'
        base = user.read_text() if user.exists() else (ROOT / 'user.js').read_text() if chrome != 'none' else ''
        user.write_text(base + prefs_js(prefs))
        process = subprocess.Popen([*display.firefox, '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env={**display.firefox_env, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'},
                                   **display.popen_options(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script("return {version:Services.appinfo.version, build:Services.appinfo.appBuildID,"
                                    "profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path,"
                                    "theme:Services.prefs.getStringPref('extensions.activeThemeID','')}",
                                    sandbox='system')
            if Path(meta['profile']).resolve() != profile.resolve():
                raise RuntimeError(f'wrong profile {meta}')
            if theme:
                meta['themeState'] = ff_themes.apply(m, theme)
            display.bind(m)
            m.execute_script("""
                const titles = ['first', 'second'];
                const urls = titles.map(t => 'data:text/html,<title>' + t + '</title><body style="background:teal">' + t);
                const triggeringPrincipal = Services.scriptSecurityManager.getSystemPrincipal();
                const old = [...gBrowser.tabs];
                const tabs = urls.map(u => gBrowser.addTab(u, {triggeringPrincipal}));
                gBrowser.selectedTab = tabs[0];
                for (const t of old) gBrowser.removeTab(t);
                """, sandbox='system')
            time.sleep(3)
            m.actions.sequence('pointer', 'mouse', {'pointerType': 'mouse'}).pointer_move(
                2, 400, origin='viewport').perform()
            time.sleep(1)
            before = m.execute_script(STATE_JS, sandbox='system')
            r = before['tabRect']
            m.actions.sequence('pointer', 'mouse', {'pointerType': 'mouse'}).pointer_move(
                round(r['x'] + r['width'] / 2), round(r['y'] + r['height'] / 2), origin='viewport').perform()
            state = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                time.sleep(.2)
                state = m.execute_script(STATE_JS, sandbox='system')
                if state['state'] == 'open':
                    break
            painted = None
            if state['state'] == 'open':
                time.sleep(.5)
                state = m.execute_script(STATE_JS, sandbox='system')
                shown = display.capture(shots / 'open.ppm')
                m.actions.sequence('pointer', 'mouse', {'pointerType': 'mouse'}).pointer_move(
                    2, 400, origin='viewport').perform()
                deadline = time.monotonic() + 5
                while m.execute_script(STATE_JS, sandbox='system')['state'] != 'closed' and time.monotonic() < deadline:
                    time.sleep(.2)
                time.sleep(.5)
                hidden = display.capture(shots / 'closed.ppm')
                w, h, a = read_ppm(shown)
                _, _, b = read_ppm(hidden)
                pr = state['rect']
                x0, y0 = max(0, int(pr['x'])), max(0, int(pr['y']))
                x1, y1 = min(w, int(pr['x'] + pr['width'])), min(h, int(pr['y'] + pr['height']))
                total = changed = 0
                for y in range(y0, y1, 2):
                    for x in range(x0, x1, 2):
                        i = (y * w + x) * 3
                        total += 1
                        changed += a[i:i + 3] != b[i:i + 3]
                painted = {'sampled': total, 'changed': changed}
            return {'meta': meta, 'before': before, 'after': state, 'painted': painted}
        finally:
            try:
                if m:
                    m.delete_session()
            finally:
                display.stop_firefox(process)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--profile', type=Path, help='copy this profile per case (lock files and caches left out)')
    p.add_argument('--chrome', action='append', help="repo (default), none, keep (the profile's own) or a DIR")
    p.add_argument('--nova', action='append', choices=('false', 'true', 'keep'))
    p.add_argument('--layout', action='append', choices=('horizontal', 'vertical', 'keep'))
    p.add_argument('--theme', help='ff_themes spec to enable')
    p.add_argument('--config', type=Path, help='config.css for repo/DIR chrome')
    a = p.parse_args()
    config = a.config.read_text() if a.config else None
    failures = 0
    with tempfile.TemporaryDirectory(prefix='tf-preview-display-') as d:
        display = open_display(d, os.environ.copy())
        try:
            for chrome in a.chrome or ['repo']:
                for nova in a.nova or ['false', 'true']:
                    for layout in a.layout or ['horizontal', 'vertical']:
                        case = {'chrome': chrome, 'nova': nova, 'layout': layout, 'theme': a.theme}
                        shots = Path(d) / f'{len(case)}-{chrome.replace("/", "_")}-{nova}-{layout}'
                        shots.mkdir(parents=True, exist_ok=True)
                        result = run_case(display, a.profile, chrome, None if nova == 'keep' else nova == 'true',
                                          None if layout == 'keep' else layout, a.theme, config, shots)
                        ok = (result['after']['state'] == 'open' and result['painted']
                              and result['painted']['changed'] > result['painted']['sampled'] * .3)
                        failures += not ok
                        print(('PASS ' if ok else 'FAIL ') + json.dumps({**case, **result}), flush=True)
        finally:
            display.close()
    print(f'{"FAIL" if failures else "PASS"}: {failures} case(s) without a painted preview')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
