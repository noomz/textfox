#!/usr/bin/env python3
"""Check container-colored frames (V18) from headed, painted pixels; exit 0 pass, 1 fail.

`--tf-container-frame: 1` paints the content box (#tabbrowser-tabbox) border in the
color the selected tab's container line paints, and each split-view panel's border in
the line color of the tab it shows; a tab without a container keeps --tf-border. `0`
(the default) keeps --tf-border everywhere.

Scenes, in both browser Nova states and with the option at 0 (shipped default) and 1:

- single: selected tab in container 2 (orange), no split view;
- single-gray: selected tab in a gray container (created by the test);
- plain: selected tab without a container;
- split-two: split view of a container 1 (blue, selected) and a container 4 (pink) tab;
- split-second: split view of a cyan and a violet container tab (both created by the
  test), the second (violet) selected;
- split-one: split view of a container 1 (blue, selected) and a container-less tab.

The expected container color is the painted color of the tab's `.tab-context-line`,
not a variable the CSS also reads. With browser Nova off, textfox's palette lacks cyan,
violet and gray, whose lines paint Firefox's own colors; there the frame takes Firefox's
`--color-<x>-40` token for cyan and violet (V18), and gray's line (the tab's text color). Each frame must paint its expected color on the left, right
and bottom border lines (test-surface-paint-live.py's border_sides), and a container
color must differ from --tf-border, so the check cannot pass by accident. Uses the T15 headless Sway display.
Requires sway, grim and the live-tool venv Python. Exits 0 or 1, never inconclusive.
"""
import argparse
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

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
_paint = runpy.run_path(str(HERE / 'test-surface-paint-live.py'))
_more = runpy.run_path(str(HERE / 'test-surface-paint-more-live.py'))
(Image, near, dominant, start_sway, stop, wait, border_sides, PARSE_JS, CHROME_STYLE_JS, FREEZE, DISTINCT,
 OUTPUT) = (_paint[k] for k in ('Image', 'near', 'dominant', 'start_sway', 'stop', 'wait', 'border_sides',
                                'PARSE_JS', 'CHROME_STYLE_JS', 'FREEZE', 'DISTINCT', 'OUTPUT'))
neutral_pointer, wait_open = _more['neutral_pointer'], _more['wait_open']

TABS = '--tf-display-horizontal-tabs: block;'
CONFIGS = {
    'off': f':root {{ {TABS} }}\n',
    'on': f':root {{ {TABS} --tf-container-frame: 1; }}\n',
}
BORDER_SEARCH = 6  # px inward from the element's border box to find the border line
OFF_PALETTE = {'cyan', 'violet'}  # colors textfox's palette lacks whose --color-<x>-40 exists with Nova off

# Containers in colors Firefox's defaults lack; ids land in window.tfContainers.
CREATE_JS = r"""
const {ContextualIdentityService} = ChromeUtils.importESModule(
  'moz-src:///toolkit/components/contextualidentity/ContextualIdentityService.sys.mjs');
window.tfContainers = {};
for (const color of ['cyan', 'violet', 'gray'])
  window.tfContainers[color] = ContextualIdentityService.create(`tf ${color}`, 'fingerprint', color).userContextId;
return window.tfContainers;
"""

# Each frame: selector, and the tab whose container it follows (JS expression) or None.
FRAMES_JS = PARSE_JS + r"""
const make = () => document.createXULElement('box');
const solid = (host, value) => {
  const bg = over(resolveColor(host, 'var(--tf-bg)', make), [0, 0, 0]);
  return over(resolveColor(host, value, make), bg);
};
const rect = e => { const r = e.getBoundingClientRect();
  return {x:Math.round(r.x), y:Math.round(r.y), width:Math.round(r.width), height:Math.round(r.height)}; };
const selected = gBrowser.selectedTab;
const frames = [];
const add = (name, element, tab) => {
  const color = [...tab.classList].find(c => c.startsWith('identity-color-'))?.slice('identity-color-'.length);
  const line = tab.querySelector('.tab-context-line');
  frames.push({name, box: rect(element), border: solid(element, 'var(--tf-border)'),
    userContextId: tab.userContextId, color, line: line && rect(line),
    token: color ? solid(document.documentElement,  // tokens compute as oklch(); color-mix yields sRGB
      `color-mix(in srgb, var(--color-${color}-40), var(--color-${color}-40))`) : null});
};
add('content-box', document.getElementById('tabbrowser-tabbox'), selected);
if (selected.splitview) {
  for (const tab of selected.splitview.tabs) {
    const panel = document.getElementById(tab.linkedPanel);
    add(tab == selected ? 'split-active' : 'split-other', panel, tab);
  }
}
return frames;
"""

SCENES = {
    'single': "gBrowser.selectedTab = gBrowser.addTrustedTab('about:blank', {userContextId: 2});",
    'single-gray': "gBrowser.selectedTab = gBrowser.addTrustedTab('about:blank', {userContextId: window.tfContainers.gray});",
    'plain': "gBrowser.selectedTab = gBrowser.addTrustedTab('about:blank');",
    'split-two': ("const a = gBrowser.addTrustedTab('about:blank', {userContextId: 1}); "
                  "const b = gBrowser.addTrustedTab('about:blank', {userContextId: 4}); "
                  "gBrowser.selectedTab = a; gBrowser.addTabSplitView([a, b], {insertBefore: a}); "
                  "gBrowser.selectedTab = a;"),
    'split-second': ("const a = gBrowser.addTrustedTab('about:blank', {userContextId: window.tfContainers.cyan}); "
                     "const b = gBrowser.addTrustedTab('about:blank', {userContextId: window.tfContainers.violet}); "
                     "gBrowser.selectedTab = a; gBrowser.addTabSplitView([a, b], {insertBefore: a}); "
                     "gBrowser.selectedTab = b;"),
    'split-one': ("const a = gBrowser.addTrustedTab('about:blank', {userContextId: 1}); "
                  "const b = gBrowser.addTrustedTab('about:blank'); "
                  "gBrowser.selectedTab = a; gBrowser.addTabSplitView([a, b], {insertBefore: a}); "
                  "gBrowser.selectedTab = a;"),
}
SCENE_READY = {
    'single': "return gBrowser.selectedTab.userContextId == 2",
    'single-gray': "return gBrowser.selectedTab.userContextId == window.tfContainers.gray",
    'plain': "return !gBrowser.selectedTab.userContextId && !gBrowser.selectedTab.splitview",
    'split-second': ("return gBrowser.selectedTab.userContextId == window.tfContainers.violet && gBrowser.selectedTab.splitview "
                     "&& document.querySelectorAll('.split-view-panel').length == 2"),
    'split-two': "return gBrowser.selectedTab.splitview && document.querySelectorAll('.split-view-panel').length == 2",
    'split-one': "return gBrowser.selectedTab.splitview && document.querySelectorAll('.split-view-panel').length == 2",
}
CLOSE = ("const keep = gBrowser.addTrustedTab('about:blank'); gBrowser.selectedTab = keep; "
         "for (const t of [...gBrowser.tabs]) if (t != keep) gBrowser.removeTab(t);")


def shoot(m, directory, env, n):
    m.execute_async_script(CHROME_STYLE_JS, script_args=[FREEZE], sandbox='system')
    time.sleep(.5)
    path = directory / f'shot-{n}.ppm'
    for _ in range(3):  # grim fails now and then while a window maps or unmaps
        if subprocess.run(['grim', '-t', 'ppm', str(path)], env=env, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            return Image(path)
        time.sleep(1)
    raise RuntimeError(f'grim could not capture {path}')


def line_color(scene, frame, image):
    """The painted color of the tab's container line (its middle third, past antialiased ends)."""
    line = frame['line']
    if not line or line['width'] < 6 or line['height'] < 1:
        raise RuntimeError(f'{scene} {frame["name"]}: no painted container line {line}')
    middle = {'x': line['x'] + line['width'] // 3, 'y': line['y'], 'width': line['width'] // 3,
              'height': line['height']}
    return list(dominant(image, middle))


def judge(scene, option, frame, image, nova):
    """Return (record, failures) for one frame."""
    expected = frame['border']
    if option == 'on' and frame['color']:
        line = line_color(scene, frame, image)
        # A token may lie outside sRGB (component below 0 or above 255); painting clips it.
        token = [max(0, min(255, round(v))) for v in frame['token']]
        expected = token if not nova and frame['color'] in OFF_PALETTE else line
        if near(expected, frame['border'], DISTINCT):
            raise RuntimeError(f'{scene} {frame["name"]}: container color {expected} too close to '
                               f'--tf-border {frame["border"]} to tell apart')
    sides = border_sides(image, frame['box'], tuple(expected), BORDER_SEARCH)
    record = {'scene': scene, 'frame': frame['name'], 'expected': expected, 'sides': sides,
              'userContextId': frame['userContextId'], 'color': frame['color'], 'box': frame['box']}
    missing = [side for side, ok in sides.items() if not ok]
    label = 'container color' if expected is not frame['border'] else '--tf-border'
    return record, [f'{scene} {frame["name"]} has no {label} {expected} border on {", ".join(missing)}'] if missing else []


def launch(env, nova, kind):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-container-frame-') as name:
        directory = Path(name)
        profile = directory / 'profile'
        profile.mkdir()
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(CONFIGS[kind])
        prefs = {
            'toolkit.legacyUserProfileCustomizations.stylesheets': True,
            'browser.nova.enabled': nova,
            'browser.shell.checkDefaultBrowser': False,
            'browser.startup.page': 0,
            'privacy.userContext.enabled': True,
            'sidebar.revamp': False,
            'sidebar.verticalTabs': False,
            'ui.prefersReducedMotion': 1,
            'marionette.port': port,
        }
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
                "buildID:Services.appinfo.appBuildID, wayland:Services.env.get('WAYLAND_DISPLAY'), dpr:devicePixelRatio, "
                "browserNova:Services.prefs.getBoolPref('browser.nova.enabled'), "
                "containers:Services.prefs.getBoolPref('privacy.userContext.enabled')}", sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or not meta['wayland'] or meta['browserNova'] != nova or meta['dpr'] != 1
                    or not meta['containers']):
                raise RuntimeError(f'unexpected Firefox process, profile, display or preference: {meta}')
            wait(m, 'return gBrowserInit.delayedStartupFinished', 'Firefox startup')
            m.execute_script("document.documentElement.removeAttribute('remotecontrol')", sandbox='system')
            meta['containers'] = m.execute_script(CREATE_JS, sandbox='system')
            records, failures, shots = [], [], 0
            for scene, script in SCENES.items():
                m.execute_script(script, sandbox='system')
                wait_open(m, SCENE_READY[scene], scene)
                m.execute_script("gBrowser.selectedBrowser.focus()", sandbox='system')
                neutral_pointer(m)
                frames = m.execute_script(FRAMES_JS, sandbox='system')
                if len(frames) != (3 if scene.startswith('split') else 1):
                    raise RuntimeError(f'{scene}: frames found {[f["name"] for f in frames]}')
                shots += 1
                image = shoot(m, directory, env, shots)
                for frame in frames:
                    record, found = judge(scene, kind, frame, image, nova)
                    records.append(record)
                    failures += found
                m.execute_script(CLOSE, sandbox='system')
                wait_open(m, "return gBrowser.tabs.length == 1 && !document.querySelector('.split-view-panel')",
                          f'{scene} closed')
            return meta, records, failures
        finally:
            try:
                if m:
                    m.delete_session()
            finally:
                stop(process, 15)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--nova', choices=['false', 'true'], action='append', help='limit to a browser Nova state')
    args = parser.parse_args()
    novas = [n == 'true' for n in args.nova] if args.nova else [False, True]
    failures = []
    with tempfile.TemporaryDirectory(prefix='tf-container-sway-') as name:
        directory = Path(name)
        directory.chmod(0o700)
        env = os.environ.copy()
        sway = start_sway(directory, env)
        try:
            for nova in novas:
                for kind in CONFIGS:
                    meta, records, found = launch(env, nova, kind)
                    print(json.dumps({'nova': nova, 'option': kind, 'firefox': meta, 'frames': records}), flush=True)
                    failures += [f'nova={nova} option={kind}: {f}' for f in found]
        finally:
            stop(sway, 10)
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', file=sys.stderr)
        return 1
    print(f'PASS: content box and split panels paint --tf-border with --tf-container-frame 0 and their tab\'s '
          f'container color with 1, browser Nova {"/".join(str(n).lower() for n in novas)}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:  # an untrustworthy run is a failure, never inconclusive
        print(f'FAIL: {error}', file=sys.stderr)
        sys.exit(1)
