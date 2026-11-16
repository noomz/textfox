#!/usr/bin/env python3
"""Check issue #3 URL bar radii and painted corners in headed disposable Firefox.

Run with the live-tool venv Python. Uses paint_display.py's platform driver.
--artifacts DIR keeps full composited captures, including the open results join.
Checks rounding 0px and 8px, Nova off and on, focused results closed and open.
Expected-radius and wrong-radius controls validate the corner pixel comparison.
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
from paint_display import open_display, read_ppm

ROOT = Path(__file__).resolve().parents[2]
BACKGROUND = '#urlbar > .urlbar-background'
MEASURE_JS = """
const bar = document.getElementById('urlbar');
const background = bar.querySelector(':scope > .urlbar-background');
const input = bar.querySelector(':scope > .urlbar-input-container');
const view = bar.querySelector(':scope > .urlbarView');
const corners = s => [s.borderTopLeftRadius, s.borderTopRightRadius,
                      s.borderBottomRightRadius, s.borderBottomLeftRadius];
const rect = e => { const r = e.getBoundingClientRect();
  return {x:r.x, y:r.y, width:r.width, height:r.height}; };
const s = getComputedStyle(background), i = getComputedStyle(input), v = getComputedStyle(view);
return {focused:bar.hasAttribute('focused'), open:bar.hasAttribute('open'),
  active:document.activeElement.id, rounding:s.getPropertyValue('--tf-rounding').trim(),
  radius:corners(s), outline:{style:s.outlineStyle, width:s.outlineWidth, color:s.outlineColor},
  background:rect(background), input:rect(input), inputRadius:corners(i),
  inputOutline:i.outlineStyle, view:rect(view), viewRadius:corners(v),
  viewBorder:v.borderTopWidth, viewMarginTop:v.marginTop};
"""


def wait(m, condition):
    deadline = time.monotonic() + 20
    while not m.execute_script(condition, sandbox='system'):
        if time.monotonic() >= deadline:
            raise RuntimeError(f'URL bar state did not settle: {condition}')
        time.sleep(.1)


def corner_pixels(path, rect, dpr):
    width, height, image = read_ppm(path)
    x, y = round(rect['x'] * dpr), round(rect['y'] * dpr)
    right = round((rect['x'] + rect['width']) * dpr)
    pad, span = round(4 * dpr), round(32 * dpr)
    points = [(xx, yy) for yy in range(y - pad, y + round(8 * dpr))
              for left in (x - pad, right - span)
              for xx in range(left, left + span + pad)]
    if any(xx < 0 or yy < 0 or xx >= width or yy >= height for xx, yy in points):
        raise RuntimeError(f'URL bar corners outside capture: {rect}')
    return [tuple(image[(yy * width + xx) * 3:(yy * width + xx) * 3 + 3]) for xx, yy in points]


def changed(first, second):
    return sum(max(abs(a - b) for a, b in zip(p, q)) > 2 for p, q in zip(first, second))


def check(display, directory, artifacts, nova, rounding):
    label = f'nova-{str(nova).lower()}-rounding-{rounding}'
    profile = directory / label
    profile.mkdir()
    shutil.copytree(ROOT / 'chrome', profile / 'chrome')
    (profile / 'chrome' / 'config.css').write_text(
        f':root {{ --tf-rounding: {rounding}px; --tf-bg: rgb(24, 36, 64); '
        '--tf-fg: rgb(255, 255, 255); --tf-border: rgb(200, 0, 90); '
        '--tf-accent: rgb(250, 200, 0); }\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
             'toolkit.legacyUserProfileCustomizations.stylesheets': True,
             'browser.shell.checkDefaultBrowser': False, 'ui.prefersReducedMotion': 1}
    (profile / 'user.js').write_text(''.join(
        f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
    env = dict(display.firefox_env, MOZ_REMOTE_ALLOW_SYSTEM_ACCESS='1')
    env.pop('MOZ_HEADLESS', None)
    process = subprocess.Popen(display.firefox + ['--marionette', '-remote-allow-system-access',
                               '--new-instance', '--profile', str(profile), 'about:blank'],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               **display.popen_options())
    m, failures = None, []
    try:
        m = connect(port)
        m.set_context('chrome')
        display.bind(m)
        meta = m.execute_script("""
            return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path,
              version:Services.appinfo.version, build:Services.appinfo.appBuildID,
              nova:Services.prefs.getBoolPref('browser.nova.enabled'), os:Services.appinfo.OS,
              headless:Cc['@mozilla.org/gfx/info;1'].getService(Ci.nsIGfxInfo).isHeadless,
              dpr:devicePixelRatio, wayland:Services.env.get('WAYLAND_DISPLAY'),
              windowProtocol:ChromeUtils.windowProtocol};
            """, sandbox='system')
        if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                or meta['nova'] != nova or meta['headless'] or display.check(meta)):
            raise RuntimeError(f'wrong Firefox process, profile or display: {meta}')
        m.execute_script("""
            const style = document.createElement('style');
            style.textContent = '* { caret-color: transparent !important; }';
            document.head.appendChild(style);
            document.documentElement.removeAttribute('remotecontrol');
            gURLBar.focus(); gURLBar.view.close();
            """, sandbox='system')
        for opened in (False, True):
            state = f'{label}-{"open" if opened else "closed"}'
            if opened:
                m.execute_script("gURLBar.search('mozilla')", sandbox='system')
                wait(m, "return !!document.querySelector('#urlbar[open] .urlbarView-row')")
            else:
                wait(m, "return gURLBar.hasAttribute('focused') && !gURLBar.hasAttribute('open')")
            actual = m.execute_script(MEASURE_JS, sandbox='system')
            if not actual['focused'] or actual['open'] != opened or actual['active'] != 'urlbar-input':
                raise RuntimeError(f'{state}: wrong focus or open state: {actual}')
            if actual['rounding'] != f'{rounding}px':
                raise RuntimeError(f'{state}: config.css not applied: {actual}')
            rings = {'background': actual['background']}
            if opened and actual['inputOutline'] == 'solid':
                rings['input'] = actual['input']
            for ring in rings:
                radius = actual['radius' if ring == 'background' else 'inputRadius']
                if radius != [f'{rounding}px'] * 4:
                    failures.append(f'{state}: {ring} focused radius expected {[f"{rounding}px"] * 4}, got {radius}')
            if actual['outline'] != {'style': 'solid', 'width': '2px', 'color': 'rgb(250, 200, 0)'}:
                failures.append(f'{state}: focus outline changed: {actual["outline"]}')
            if opened and (actual['viewRadius'] != ['0px', '0px', f'{rounding}px', f'{rounding}px']
                           or actual['viewBorder'] != '2px'):
                failures.append(f'{state}: results border or corners changed: {actual}')

            def capture(name, radius=None):
                m.execute_script("""
                    const utils = window.windowUtils;
                    if (window._tfRadiusSheet) {
                      utils.removeSheetUsingURIString(window._tfRadiusSheet, utils.USER_SHEET);
                      window._tfRadiusSheet = null;
                    }
                    if (arguments[1] !== null) {
                      const css = ':root #urlbar[focused] > :is(.urlbar-background, .urlbar-input-container) '
                        + '{ border-radius: ' + arguments[1] + 'px !important; }';
                      window._tfRadiusSheet = 'data:text/css,' + encodeURIComponent(css);
                      utils.loadSheetUsingURIString(window._tfRadiusSheet, utils.USER_SHEET);
                      if (getComputedStyle(document.querySelector(arguments[0])).borderRadius !== arguments[1] + 'px')
                        throw new Error('radius paint control did not override userChrome.css');
                    }
                    """, script_args=[BACKGROUND, radius], sandbox='system')
                time.sleep(.3)
                path = artifacts / f'{state}-{name}.ppm'
                display.capture(path)
                return {ring: corner_pixels(path, rect, meta['dpr']) for ring, rect in rings.items()}

            baseline = capture('actual')
            control = capture('control')
            expected = capture('expected', rounding)
            wrong = capture('wrong', 24 if rounding == 0 else 0)
            restored = capture('restored')
            pixels = {}
            for ring in rings:
                difference = {'control': changed(baseline[ring], control[ring]),
                              'expected': changed(baseline[ring], expected[ring]),
                              'wrongVsExpected': changed(expected[ring], wrong[ring]),
                              'restored': changed(baseline[ring], restored[ring])}
                pixels[ring] = difference
                if difference['control'] or difference['restored']:
                    raise RuntimeError(f'{state}: unstable {ring} corner captures: {difference}')
                if not difference['wrongVsExpected']:
                    raise RuntimeError(f'{state}: {ring} wrong-radius paint control undetected: {difference}')
                if difference['expected']:
                    failures.append(f'{state}: {ring} painted corners differ from configured radius '
                                    f'at {difference["expected"]} pixels')
            print(json.dumps({'case': state, 'firefox': meta, 'actual': actual, 'cornerPixels': pixels}), flush=True)
        return failures
    finally:
        try:
            if m:
                m.delete_session()
        finally:
            display.stop_firefox(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path)
    args = parser.parse_args()
    failures = []
    with tempfile.TemporaryDirectory(prefix='tf-urlbar-radius-') as name:
        directory = Path(name)
        directory.chmod(0o700)
        artifacts = args.artifacts.resolve() if args.artifacts else directory / 'captures'
        artifacts.mkdir(parents=True, exist_ok=True)
        display = open_display(directory, os.environ.copy())
        try:
            for nova in (False, True):
                for rounding in (0, 8):
                    failures += check(display, directory, artifacts, nova, rounding)
        finally:
            display.close()
    for failure in failures:
        print(f'FAIL {failure}', file=sys.stderr)
    if failures:
        print(f'FAIL: {len(failures)} assertions across 8 focused URL bar cases', file=sys.stderr)
        return 1
    print('PASS: 8 focused URL bar cases; configured radii, painted corners, focus outlines and results borders; wrong-radius paint controls detected')
    return 0


if __name__ == '__main__':
    sys.exit(main())
