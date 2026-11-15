#!/usr/bin/env python3
"""Painted theme art (V19, T33): the art shows, animates, and titles keep step with it.

`--tf-theme-images` 1 keeps a Firefox theme's images and gradients; `--tf-theme-gradient`
1 keeps browser Nova's own gradient (no theme). textfox paints the art once on the window
and each section title paints it again, fixed to the window. Harness: T15's disposable
headless Sway compositor (`paint_display.py`), Firefox headed inside it, classic layout
with horizontal tabs, bookmarks toolbar, bookmarks sidebar and findbar shown.

The option is on in config.css; an author sheet sets both options to 0 for the "off"
captures. Captures pair up under a paused refresh driver (`advanceTimeAndRefresh`), so
both of a pair show the same animation frame. For each setup, both browser Nova states:

- off is as before: with the options at 0 the window paints the same pixels as the chrome
  at `--baseline` (default c2c4b79, before T33) in the same state;
- art shows: inside each box (navbar, tabs, bookmarks, sidebar, main, find), pixels that
  paint flat --tf-bg (the theme's frame color by default) with the option off must paint
  something else with it on: at least ART_MIN in every box but those FLAT lists, where the
  theme's gradient equals its frame color (nova-ash light at the sidebar);
- animates (the starfield): with the refresh driver running, two captures 0.7 s apart
  differ inside the navbar, and at least two of the three paused frames differ there;
- titles in step: at three paused frames, each title with its text made transparent
  paints exactly what the window paints with the title hidden (within BLEND), across the
  title's box except the border rows it covers. Text shadows are cleared with the text
  (lwtheme-image themes give toolbar text one). A title out of step with the window's
  animation, or painting --tf-bg, shows as a patch. In the border rows under it, the
  title must differ from that capture (which shows the line) in HIDES of the pixels.

`--identity` instead compares, with no option in config.css at all, the window with the
chrome at `--baseline` for no theme and ff_themes.DEFAULT_THEMES, both browser Nova states,
classic and revamp layouts: the pixels must be the same.

Setups: `--art images` with each `--theme` (default: ff_themes.ART_THEMES), `--art
gradient` without a theme (browser Nova on only: Linux paints the real gradient
transparent, so the run stands in ff_themes.TEST_GRADIENT). `--chrome REF` takes chrome/
from a git ref (a red run on the code before the change). Exit 0 or 1, never inconclusive.

Run: "$HOME/.cache/textfox-firefox-live/venv/bin/python" tools/firefox-chrome/test-theme-art-live.py
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import paint_display  # noqa: E402
import ff_themes  # noqa: E402

_titles = runpy.run_path(str(HERE / 'test-titles-paint-live.py'))
connect, wait, free_port, LAYOUTS, HOSTS_JS, FREEZE = (
    _titles[k] for k in ('connect', 'wait', 'free_port', 'LAYOUTS', 'HOSTS_JS', 'FREEZE'))

BOXES = ['navbar', 'tabs-horizontal', 'bookmarks', 'sidebar', 'main', 'find']
SELECTORS = {name: selector for name, (selector, _, _) in _titles['TITLES'].items()}
TOLERANCE = 4   # per channel: flat --tf-bg vs painted
BLEND = 4       # per channel: title vs the window's paint without it
ART_MIN = 20    # pixels per box where the art must paint
# Boxes where a theme's art equals its frame color, so nothing can show (measured, T33).
FLAT = {'nova-ash@mozilla.org:light': {'sidebar'}}
HIDES = 0.9     # share of the border line under a title the title must cover
NOISE = 2       # per channel: identical captures
STEP = 333      # ms of animation between paused frames
STEPS = [0, STEP, STEP]  # three paused frames for the title check
OFF = ':root { --tf-theme-images: 0 !important; --tf-theme-gradient: 0 !important; }'
BASELINE = 'c2c4b79'

SHEET_JS = r"""
const [css, step] = arguments;
const utils = window.windowUtils;
if (window._tfArtSheet) utils.removeSheetUsingURIString(window._tfArtSheet, utils.AUTHOR_SHEET);
window._tfArtSheet = 'data:text/css,' + encodeURIComponent(css);
utils.loadSheetUsingURIString(window._tfArtSheet, utils.AUTHOR_SHEET);
if (step !== null) utils.advanceTimeAndRefresh(step);
return true;
"""

STATE_JS = HOSTS_JS + r"""
const out = {};
for (const name of arguments[0]) {
  const host = HOSTS[name]();
  if (!host) return {missing: name};
  const r = host.getBoundingClientRect(), s = getComputedStyle(host);
  out[name] = {x: r.x, y: r.y, w: r.width, h: r.height, border: parseFloat(s.borderTopWidth)};
}
const probe = document.createXULElement('box');
document.body.append(probe);
probe.style.color = 'var(--tf-bg)';
const bg = getComputedStyle(probe).color;
probe.style.color = 'var(--lwt-accent-color, var(--tf-bg))';
const frame = getComputedStyle(probe).color;
probe.remove();
return {boxes: out, bg, frame};
"""


def rgb(text):
    """'rgb(r, g, b)' or 'color(srgb r g b)' (0..1) as an (r, g, b) tuple of 0..255."""
    inner = text[text.index('(') + 1:text.rindex(')')].replace(',', ' ').replace('/', ' ').split()
    if inner[0] == 'srgb':
        return tuple(round(float(v) * 255) for v in inner[1:4])
    return tuple(round(float(v)) for v in inner[:3])


def chrome_from(ref, directory):
    """chrome/ as committed at a git ref, extracted under directory."""
    target = Path(directory) / f'chrome-{ref}'
    target.mkdir()
    archive = subprocess.run(['git', '-C', str(ROOT), 'archive', ref, 'chrome'], check=True, capture_output=True)
    subprocess.run(['tar', '-x', '-C', str(target)], input=archive.stdout, check=True)
    return target / 'chrome'


def near(a, b, tolerance):
    return all(abs(p - q) <= tolerance for p, q in zip(a, b))


class Window:
    def __init__(self, display, directory, chrome, config, theme, nova, layout='classic'):
        self.display, self.directory, self.n = display, Path(directory), 0
        port = free_port()
        profile = self.directory / 'profile'
        profile.mkdir()
        shutil.copytree(chrome, profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(':root { --tf-display-horizontal-tabs: block; }\n' + config)
        prefs = {
            'toolkit.legacyUserProfileCustomizations.stylesheets': True,
            'browser.nova.enabled': nova,
            'browser.shell.checkDefaultBrowser': False,
            'browser.startup.page': 0,
            'browser.toolbars.bookmarks.visibility': 'always',
            'ui.prefersReducedMotion': 1,
            'marionette.port': port,
            **LAYOUTS[layout],
            **(ff_themes.prefs(theme) if theme else {}),
        }
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        self.process = subprocess.Popen(
            display.firefox + ['--marionette', '-remote-allow-system-access', '--new-instance',
                               '--profile', str(profile), 'about:blank'],
            env={**display.firefox_env, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **display.popen_options())
        self.m = None
        try:
            self.m = connect(port)
            self.m.set_context('chrome')
            wait(self.m, 'return gBrowserInit.delayedStartupFinished', 'Firefox startup')
            self.meta = self.m.execute_script(
                "return {version: Services.appinfo.version, build: Services.appinfo.appBuildID, dpr: devicePixelRatio,"
                " nova: Services.prefs.getBoolPref('browser.nova.enabled')}", sandbox='system')
            if self.meta['nova'] != nova or self.meta['dpr'] != 1:
                raise RuntimeError(f'unexpected Firefox state: {self.meta}')
            if theme:
                self.meta['theme'] = ff_themes.apply(self.m, theme)
            self.m.execute_script("document.documentElement.removeAttribute('remotecontrol'); "
                                  "gBrowser.addTrustedTab('about:blank'); "
                                  "SidebarController.show('viewBookmarksSidebar')", sandbox='system')
            self.m.execute_async_script("const done = arguments[0]; gBrowser.getFindBar().then(f => { f.open(); "
                                        "setTimeout(() => done(true), 500); })", sandbox='system')
            self.m.actions.sequence('pointer', 'mouse', {'pointerType': 'mouse'}).pointer_move(
                2, 2, origin='viewport').perform()
            self.m.execute_script("document.activeElement?.blur(); gBrowser.selectedBrowser.focus()", sandbox='system')
            time.sleep(2)
        except BaseException:
            self.close()
            raise

    def shoot(self, css, step=0):
        """Capture with css loaded; step: ms to advance the paused refresh driver, or None to leave it."""
        self.m.execute_script(SHEET_JS, script_args=[FREEZE + css, step], sandbox='system')
        time.sleep(.6)
        self.n += 1
        return self.display.capture(self.directory / f'shot-{self.n}.ppm')

    def close(self):
        try:
            if self.m:
                self.m.delete_session()
        finally:
            self.display.stop_firefox(self.process)


def pixels(path):
    width, height, data = paint_display.read_ppm(path)
    return width, height, data


def count(a, b, region, tolerance, skip_rows=None, where=None):
    """Pixels in region (x0, y0, x1, y1) differing by more than tolerance; rows in skip_rows not
    counted; where(pa) limits to pixels whose first-capture color passes it. Returns (differ, total)."""
    width, height, da = a
    _, _, db = b
    x0, y0, x1, y1 = region
    differ = total = 0
    for y in range(max(0, y0), min(height, y1)):
        if skip_rows and skip_rows[0] <= y <= skip_rows[1]:
            continue
        for x in range(max(0, x0), min(width, x1)):
            i = (y * width + x) * 3
            pa, pb = da[i:i + 3], db[i:i + 3]
            if where and not where(pa):
                continue
            total += 1
            differ += max(abs(pa[k] - pb[k]) for k in range(3)) > tolerance
    return differ, total


def interior(box):
    inset = round(box['border']) + 2
    return (round(box['x']) + inset, round(box['y']) + inset,
            round(box['x'] + box['w']) - inset, round(box['y'] + box['h']) - inset)


def bbox(a, b, noise=NOISE, region=None):
    width, height, da = a
    _, _, db = b
    x0, y0, x1, y1 = region or (0, 0, width, height)
    xs, ys = [], []
    for y in range(max(0, y0), min(height, y1)):
        row = y * width * 3
        if da[row:row + width * 3] == db[row:row + width * 3]:
            continue
        for x in range(max(0, x0), min(width, x1)):
            i = row + x * 3
            if max(abs(da[i + k] - db[i + k]) for k in range(3)) > noise:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1) if xs else None


def check(display, directory, chrome, baseline, art, theme, nova, animated):
    record, failures = {'theme': theme, 'nova': nova, 'art': art}, []
    old = None
    if baseline:  # alone: Sway tiles two windows side by side
        with tempfile.TemporaryDirectory(dir=directory) as name:
            before = Window(display, name, baseline, ff_themes.ART_CONFIG[art], theme, nova)
            try:
                old = pixels(before.shoot(OFF, 0))
            finally:
                before.close()
    with tempfile.TemporaryDirectory(dir=directory) as name:
        window = Window(display, name, chrome, ff_themes.ART_CONFIG[art], theme, nova)
        try:
            record['firefox'] = window.meta
            record['firefox']['art'] = ff_themes.art_expected(window.m, art)
            if not record['firefox']['art']['expected']:
                raise RuntimeError(f'Firefox has no {art} art in this setup: {record["firefox"]["art"]}')
            state = window.m.execute_script(STATE_JS, script_args=[BOXES], sandbox='system')
            if 'missing' in state:
                raise RuntimeError(f'{state["missing"]}: box not shown')
            boxes, bg, frame = state['boxes'], rgb(state['bg']), rgb(state['frame'])
            record['colors'] = {'bg': bg, 'frame': frame}

            if animated:
                first = pixels(window.shoot('', None))
                time.sleep(.7)
                second = pixels(window.shoot('', None))
                moved, total = count(first, second, interior(boxes['navbar']), NOISE)
                record['animatesFreely'] = moved
                if not moved:
                    failures.append(f'navbar: art does not animate with the refresh driver running ({total} pixels)')

            off = window.shoot(OFF, 0)
            on = window.shoot('', 0)
            off_px, on_px = pixels(off), pixels(on)
            record['art'] = {}
            for name in BOXES:
                differ, total = count(off_px, on_px, interior(boxes[name]), TOLERANCE,
                                      where=lambda p: near(p, bg, TOLERANCE))
                onframe, _ = count(off_px, on_px, interior(boxes[name]), TOLERANCE,
                                   where=lambda p: near(p, frame, TOLERANCE))
                record['art'][name] = {'differ': differ, 'flat': total, 'differFromFrame': onframe}
            bare = [name for name, v in record['art'].items()
                    if (v['differ'] < ART_MIN or v['differFromFrame'] < ART_MIN) and name not in FLAT.get(theme, ())]
            if bare:
                failures.append(f'theme art paints fewer than {ART_MIN} pixels off --tf-bg and frame color {frame} '
                                f'in {", ".join(bare)}: {record["art"]}')

            if old:
                region = (0, 0, old[0], old[1])
                changed, total = count(old, off_px, region, NOISE)
                record['offVsBaseline'] = changed
                if changed:
                    box = bbox(old, off_px)
                    failures.append(f'options 0 paint {changed} pixels unlike the chrome at the baseline, in {box}')

            frames = []
            record['titles'] = {}
            for step in STEPS:
                window.shoot('', step)
                shots = {}
                for name in BOXES:
                    selector = SELECTORS[name]
                    shown = pixels(window.shoot('', 0))
                    clear = pixels(window.shoot(f'{selector}::before {{ color: transparent !important; '
                                                'text-shadow: none !important; }}', 0))
                    hidden = pixels(window.shoot(f'{selector}::before {{ visibility: hidden !important; }}', 0))
                    b = boxes[name]  # the title straddles the top border, within the box's width
                    title = bbox(shown, hidden, region=(round(b['x']), round(b['y']) - 30,
                                                        round(b['x'] + b['w']), round(b['y']) + 30))
                    if not title:
                        raise RuntimeError(f'{name}: title paints nothing')
                    top = round(boxes[name]['y'])
                    rows = (top - 1, top + round(boxes[name]['border']))
                    patch, total = count(clear, hidden, title, BLEND, skip_rows=rows)
                    covered, line = count(clear, hidden, (title[0], top, title[2], rows[1]), BLEND)
                    record['titles'].setdefault(name, []).append({'box': title, 'patch': patch, 'of': total,
                                                                  'lineHidden': covered, 'line': line})
                    if covered < HIDES * line:
                        failures.append(f'{name}: title shows the border line under it at frame {len(frames)}: '
                                        f'covers {covered} of {line} pixels')
                    if patch:
                        failures.append(f'{name}: title paints a patch over the theme art at frame '
                                        f'{len(frames)}: {patch} of {total} pixels')
                    shots[name] = shown
                frames.append(shots)
            if animated:
                stepped = [count(frames[i - 1]['navbar'], frames[i]['navbar'], interior(boxes['navbar']), NOISE)[0]
                           for i in range(1, len(frames))]
                record['animatesStepped'] = stepped
                if not any(stepped):  # frame delays vary; the title check needs two different frames
                    failures.append(f'navbar: paused frames {STEP} ms apart never differ: {stepped}')
        finally:
            window.close()
    return record, failures


def identical(display, directory, chrome, baseline, theme, nova, layout):
    """Pixels that differ between the baseline chrome and chrome, no option in config.css."""
    shots = []
    for source in (baseline, chrome):  # one at a time: Sway tiles two windows side by side
        with tempfile.TemporaryDirectory(dir=directory) as name:
            window = Window(display, name, source, '', theme, nova, layout)
            try:
                shots.append(pixels(window.shoot('', 0)))
            finally:
                window.close()
    old, new = shots
    changed, _ = count(old, new, (0, 0, old[0], old[1]), NOISE)
    return changed, (bbox(old, new) if changed else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--art', choices=('images', 'gradient'), action='append',
                        help='option to check (repeatable; default both)')
    parser.add_argument('--theme', action='append', default=[], help='theme for --art images (default ART_THEMES)')
    parser.add_argument('--nova', choices=('false', 'true'))
    parser.add_argument('--chrome', help='check chrome/ at this git ref instead of the working tree')
    parser.add_argument('--baseline', default=BASELINE, help='git ref the options-off paint must match ("" skips)')
    parser.add_argument('--identity', action='store_true',
                        help='only compare with --baseline, no option set, default themes, both layouts')
    args = parser.parse_args()
    if args.identity:
        return identity(args)
    arts = args.art or ['images', 'gradient']
    themes = args.theme or ff_themes.ART_THEMES
    for spec in themes:
        ff_themes.parse(spec)
    novas = [args.nova == 'true'] if args.nova else [False, True]
    setups = [('images', t, n) for t in themes for n in novas] if 'images' in arts else []
    if 'gradient' in arts and True in novas:
        setups.append(('gradient', None, True))  # browser Nova's gradient exists only with the pref on
    failures = []
    with tempfile.TemporaryDirectory(prefix='tf-art-') as directory:
        chrome = chrome_from(args.chrome, directory) if args.chrome else ROOT / 'chrome'
        baseline = chrome_from(args.baseline, directory) if args.baseline else None
        (Path(directory) / 'sway').mkdir(mode=0o700)
        display = paint_display.open_display(Path(directory) / 'sway', dict(os.environ))
        try:
            for art, theme, nova in setups:
                animated = theme == ff_themes.STARFIELD
                record, problems = check(display, directory, chrome, baseline, art, theme, nova, animated)
                print(json.dumps(record), flush=True)
                failures += [f'{art} theme={theme} nova={nova}: {p}' for p in problems]
        except Exception as error:  # noqa: BLE001 - any harness failure fails the run
            for failure in failures:
                print(f'FAIL {failure}', flush=True)
            print(f'FAIL: {error}', flush=True)
            return 1
        finally:
            display.close()
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', flush=True)
        return 1
    print(f'PASS: theme art paints in every box (but FLAT ones), titles hide the line and keep step with it at {len(STEPS)} frames, the starfield '
          f'animates, options 0 paint as {args.baseline or "(no baseline)"} '
          f'({len(setups)} setups: {", ".join(f"{a}/{t or "no theme"}/nova={n}" for a, t, n in setups)})', flush=True)
    return 0


def identity(args):
    if not args.baseline:
        raise SystemExit('--identity needs --baseline')
    novas = [args.nova == 'true'] if args.nova else [False, True]
    setups = [(t, n, la) for t in [None, *ff_themes.DEFAULT_THEMES] for n in novas for la in LAYOUTS]
    failures = []
    with tempfile.TemporaryDirectory(prefix='tf-art-') as directory:
        chrome = chrome_from(args.chrome, directory) if args.chrome else ROOT / 'chrome'
        baseline = chrome_from(args.baseline, directory)
        (Path(directory) / 'sway').mkdir(mode=0o700)
        display = paint_display.open_display(Path(directory) / 'sway', dict(os.environ))
        try:
            for theme, nova, layout in setups:
                changed, box = identical(display, directory, chrome, baseline, theme, nova, layout)
                print(json.dumps({'theme': theme, 'nova': nova, 'layout': layout, 'changed': changed, 'box': box}),
                      flush=True)
                if changed:
                    failures.append(f'theme={theme} nova={nova} {layout}: {changed} pixels unlike {args.baseline} '
                                    f'in {box}')
        except Exception as error:  # noqa: BLE001 - any harness failure fails the run
            failures.append(f'harness: {error}')
        finally:
            display.close()
    for failure in failures:
        print(f'FAIL {failure}', flush=True)
    if failures:
        return 1
    print(f'PASS: with no option set the window paints as {args.baseline} ({len(setups)} setups: no theme and '
          f'{", ".join(ff_themes.DEFAULT_THEMES)}, browser Nova {"/".join(str(n).lower() for n in novas)}, '
          f'{", ".join(LAYOUTS)})', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
