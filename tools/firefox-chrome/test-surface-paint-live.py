#!/usr/bin/env python3
"""Check textfox's key surfaces from headed, painted pixels; exit 0 pass, 1 fail.

Starts a disposable 1280x800 headless Sway compositor, runs Firefox headed in
it, and captures the compositor output with grim (see test-nova-layout-live.py).
Each surface is hidden in turn with `visibility: hidden` (layout kept); the
pixels that change are the pixels it painted, and their bounding box is the
surface on screen. Inside that box the capture must show:

- chrome surfaces: --tf-bg as the dominant color; a border of the surface's
  configured variable (--tf-border for navbar, tabs and findbar, --tf-accent for
  URL bar results, app menu and context menu), distinct from the background, on
  the left, right and bottom edges (the top edge carries the section title);
  and a textfox-colored text target (hidden in its own capture) painting at
  least MIN_TEXT_PIXELS pixels at 4.5:1 against the painted background;
- new-tab search box and first shortcut tile: a border of the resolved
  --newtab-border-color on the left, right and bottom edges.

Colors are resolved inside each surface, so currentColor-relative defaults
(the default --tf-border) resolve as painted; translucent colors are composited
over --tf-bg.

Surfaces: navbar, URL bar text, URL bar results, horizontal tabs, app menu,
toolbar context menu, findbar, new-tab search box and shortcut tile, in both
browser Nova states. Cases: the shipped defaults, a sentinel config.css with
distinct colors (or `--config FILE` in its place), and a known-bad config.css
that hides text and borders. Every surface must pass the good cases and fail the
known-bad case; a check that misses the known-bad config, or unstable captures,
fail the run. Exits 0 or 1, never inconclusive.

Requires sway, grim, ImageMagick, and the live-tool venv Python. New-tab feeds
are on, so the disposable profile may make network requests. Set
TF_SURFACE_ARTIFACTS to a directory to keep captures and masks.
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
_layout = runpy.run_path(str(HERE / 'test-nova-layout-live.py'))
start_sway, stop, changed_mask, count, painted_box, OUTPUT = (
    _layout[k] for k in ('start_sway', 'stop', 'changed_mask', 'count', 'painted_box', 'OUTPUT'))

MIN_RATIO = 4.5
MIN_TEXT_PIXELS = 30
TOLERANCE = 4      # per channel, painted vs expected color
DISTINCT = 12      # a border must differ from the background by more than this in some channel
EDGE_SEARCH = 24   # px inward from the painted edge to look for the border line (past shadows)
EDGE_MATCH = 0.9   # share of the middle of an edge line that must be border color
SLACK = 48         # px a surface may paint beyond its element box (section titles, popup shadows)

# Horizontal tabs are hidden by default; every case shows them so the tab strip is measured.
TABS = '--tf-display-horizontal-tabs: block;'
CONFIGS = {
    'defaults': f':root {{ {TABS} }}\n',
    # Distinct colors; the border colors stay below 4.5:1 against the background.
    'sentinel': (f':root {{ {TABS} --tf-bg: rgb(24, 36, 64); --tf-border: rgb(200, 0, 90); '
                 '--tf-accent: rgb(0, 140, 90); }\n'),
    # Known bad: text the color of the background, borders gone.
    'known-bad': f':root {{ {TABS} --tf-fg: var(--tf-bg); --tf-border-width: 0px; }}\n',
}

OPEN_URLBAR = ("gURLBar.focus(); gURLBar.search('about:')",
               "return gURLBar.view.isOpen && !!document.querySelector('.urlbarView-row .urlbarView-title')")
OPEN_APP_MENU = ("gURLBar.view.close(); gURLBar.blur(); PanelUI.show()",
                 "return document.getElementById('appMenu-popup').state == 'open'")
OPEN_CONTEXT = ("PanelUI.hide(); document.getElementById('toolbar-context-menu')"
                ".openPopup(document.getElementById('nav-bar'), 'after_start')",
                "return document.getElementById('appMenu-popup').state == 'closed' && "
                "document.getElementById('toolbar-context-menu').state == 'open'")
# name, selector hidden to find its paint, border variable (None: no border), text target, opener
CHROME_SURFACES = [
    ('navbar', '#nav-bar', '--tf-border', '#nav-bar::before', None),
    ('urlbar-text', '#urlbar-input', None, '#urlbar-input', None),
    ('tabs', '#TabsToolbar', '--tf-border', '.tabbrowser-tab:not([selected]) .tab-label', None),
    ('findbar', 'findbar:not([hidden])', '--tf-border', 'findbar:not([hidden]) .findbar-highlight', None),
    ('urlbar-results', '.urlbarView', '--tf-accent',
     '.urlbarView-row:not([selected]) .urlbarView-title', OPEN_URLBAR),
    ('app-menu', '#appMenu-popup', '--tf-accent', '#appMenu-new-tab-button2', OPEN_APP_MENU),
    ('context-menu', '#toolbar-context-menu', '--tf-accent',
     '#toolbar-context-menu > menuitem:not([hidden])', OPEN_CONTEXT),
]
NEWTAB_SURFACES = {
    # Light-DOM in-page URL bar input (gated, browser Nova) or the shadow-DOM handoff host.
    'newtab-search': ['.search-wrapper input', '.search-wrapper content-search-handoff-ui'],
    'newtab-tile': ['.top-site-outer .tile'],
}

FREEZE = ('*, *::before, *::after { animation: none !important; transition: none !important; '
          'caret-color: transparent !important; }')

CHROME_STYLE_JS = r"""
const done = arguments[arguments.length - 1];
const utils = window.windowUtils;
if (window._tfPaintSheet) utils.removeSheetUsingURIString(window._tfPaintSheet, utils.AUTHOR_SHEET);
window._tfPaintSheet = 'data:text/css,' + encodeURIComponent(arguments[0]);
utils.loadSheetUsingURIString(window._tfPaintSheet, utils.AUTHOR_SHEET);
requestAnimationFrame(() => requestAnimationFrame(() => done(true)));
"""

CONTENT_STYLE_JS = r"""
let style = document.getElementById('tf-paint-test');
if (!style) { style = document.createElement('style'); style.id = 'tf-paint-test'; document.head.appendChild(style); }
style.textContent = arguments[0];
document.activeElement?.blur?.();
const done = arguments[arguments.length - 1];
requestAnimationFrame(() => requestAnimationFrame(() => done(true)));
"""

# Computed colors as [r, g, b, a] in 0-255; Firefox serializes color-mix() results as color(srgb ...).
PARSE_JS = r"""
const parseColor = text => {
  let m = text.match(/^color\(srgb ([-\d.e]+) ([-\d.e]+) ([-\d.e]+)(?: \/ ([-\d.e]+))?\)$/);
  if (m) return [m[1] * 255, m[2] * 255, m[3] * 255, m[4] === undefined ? 1 : +m[4]];
  m = text.match(/^rgba?\(([\d.]+), ([\d.]+), ([\d.]+)(?:, ([\d.]+))?\)$/);
  if (!m) throw new Error(`unparsed computed color ${JSON.stringify(text)}`);
  return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
};
const resolveColor = (host, value, make) => {
  const probe = make();
  host.appendChild(probe);
  probe.style.color = value;  // currentColor here is the host's color, as where the variable is used
  const color = parseColor(getComputedStyle(probe).color);
  probe.remove();
  return color;
};
const over = (c, bg) => c.slice(0, 3).map((v, i) => Math.round(v * c[3] + bg[i] * (1 - c[3])));
"""

RESOLVE_JS = PARSE_JS + r"""
let host = document.querySelector(arguments[0]);
if (!host) return null;
if (host.localName === 'input') host = host.parentElement;  // an input cannot hold the probe
const make = () => document.createXULElement('box');
const out = {'--tf-bg': over(resolveColor(host, 'var(--tf-bg)', make), [0, 0, 0])};
for (const v of ['--tf-border', '--tf-accent']) out[v] = over(resolveColor(host, `var(${v})`, make), out['--tf-bg']);
const r = document.querySelector(arguments[0]).getBoundingClientRect();
out.dom = {width:r.width, height:r.height};
return out;
"""

NEWTAB_MARK_JS = PARSE_JS + r"""
for (const old of document.querySelectorAll('[data-tf-paint-probe]')) old.removeAttribute('data-tf-paint-probe');
for (const selector of arguments[0]) {
  const e = [...document.querySelectorAll(selector)].find(x => x.getClientRects().length &&
    getComputedStyle(x).visibility === 'visible');
  if (!e) continue;
  e.setAttribute('data-tf-paint-probe', '');
  // textfox borders new-tab elements with --newtab-border-color (an opaque color-mix).
  const color = resolveColor(document.body, 'var(--newtab-border-color)', () => document.createElement('div'));
  const r = e.getBoundingClientRect();
  return {selector, dom:{width:r.width, height:r.height}, border:color.slice(0, 3).map(Math.round), alpha:color[3]};
}
return {missing:[...document.querySelectorAll('.search-wrapper, .search-wrapper *')].slice(0, 20)
  .map(e => `${e.localName}.${e.className} rects=${e.getClientRects().length} ${getComputedStyle(e).visibility}`)};
"""


def load_ppm(path):
    data = Path(path).read_bytes()
    fields, position = [], 0
    while len(fields) < 4:  # magic, width, height, maxval; grim writes no comments
        while data[position:position + 1].isspace():
            position += 1
        end = position
        while not data[end:end + 1].isspace():
            end += 1
        fields.append(data[position:end])
        position = end
    if fields[0] != b'P6' or fields[3] != b'255':
        raise RuntimeError(f'unexpected grim output header {fields}')
    return int(fields[1]), int(fields[2]), data[position + 1:]


class Image:
    def __init__(self, path):
        self.width, self.height, self.data = load_ppm(path)
        if (self.width, self.height) != OUTPUT:
            raise RuntimeError(f'unexpected compositor capture size {(self.width, self.height)}')

    def pixel(self, x, y):
        i = (y * self.width + x) * 3
        return tuple(self.data[i:i + 3])

    def coordinates(self, box):
        for y in range(box['y'], box['y'] + box['height']):
            for x in range(box['x'], box['x'] + box['width']):
                yield x, y


def near(a, b, tolerance=TOLERANCE):
    return all(abs(p - q) <= tolerance for p, q in zip(a, b))


def luminance(rgb):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4
    r, g, b = map(channel, rgb)
    return .2126 * r + .7152 * g + .0722 * b


def ratio(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + .05) / (low + .05)


def inflate(box, by):
    x, y = max(0, box['x'] - by), max(0, box['y'] - by)
    right = min(OUTPUT[0], box['x'] + box['width'] + by)
    bottom = min(OUTPUT[1], box['y'] + box['height'] + by)
    return {'x': x, 'y': y, 'width': right - x, 'height': bottom - y}


def inside(x, y, box):
    return box['x'] <= x < box['x'] + box['width'] and box['y'] <= y < box['y'] + box['height']


def dominant(image, box, skip=None):
    """Most frequent color in box, ignoring pixels inside skip (web content a surface frames)."""
    tally = {}
    for x, y in image.coordinates(box):
        if skip and inside(x, y, skip):
            continue
        p = image.pixel(x, y)
        tally[p] = tally.get(p, 0) + 1
    return max(tally, key=tally.get)


def border_sides(image, box, color):
    """Sides (left, right, bottom) whose outermost EDGE_SEARCH lines hold a border of color."""
    x0, y0, w, h = box['x'], box['y'], box['width'], box['height']
    rows = range(y0 + h // 4, y0 + h - h // 4)
    columns = range(x0 + w // 4, x0 + w - w // 4)
    lines = {
        'left': [[(x0 + d, y) for y in rows] for d in range(min(EDGE_SEARCH, w))],
        'right': [[(x0 + w - 1 - d, y) for y in rows] for d in range(min(EDGE_SEARCH, w))],
        'bottom': [[(x, y0 + h - 1 - d) for x in columns] for d in range(min(EDGE_SEARCH, h))],
    }
    return {side: any(line and sum(near(image.pixel(x, y), color) for x, y in line) >= EDGE_MATCH * len(line)
                      for line in candidates)
            for side, candidates in lines.items()}


class Capture:
    def __init__(self, m, directory, env):
        self.m, self.directory, self.env, self.shots = m, directory, env, 0

    def shoot(self, css, content):
        self.m.execute_async_script(CONTENT_STYLE_JS if content else CHROME_STYLE_JS,
                                    script_args=[FREEZE + css], sandbox='default' if content else 'system')
        time.sleep(.5)  # let the compositor commit the frame grim reads
        self.shots += 1
        path = self.directory / f'shot-{self.shots}.ppm'
        subprocess.run(['grim', '-t', 'ppm', str(path)], env=self.env, check=True, stdout=subprocess.DEVNULL)
        return path

    def surface(self, name, selector, text_selector=None, content=False):
        """Base capture, painted box of selector, and text target pixels, with stability controls."""
        hide = '{ visibility: hidden !important; }'
        base = self.shoot('', content)
        control = self.shoot('', content)
        hidden = self.shoot(f'{selector} {hide}', content)
        text = self.shoot(f'{text_selector} {hide}', content) if text_selector else None
        restored = self.shoot('', content)
        stem = self.directory / f'mask-{self.shots}'
        controls = {'control': count(changed_mask(base, control, f'{stem}-control.png')),
                    'restored': count(changed_mask(base, restored, f'{stem}-restored.png'))}
        if any(controls.values()):
            raise RuntimeError(f'{name}: captures unstable {controls}')
        mask = changed_mask(base, hidden, f'{stem}-{name}.png')
        return {'image': Image(base), 'box': painted_box(mask), 'changed': count(mask),
                'text': Image(text) if text else None}


def judge(name, shot, colors, border_var=None, border_color=None):
    """Return (record, failures) for one surface; raise when the measurement is not of that surface."""
    image, box, dom = shot['image'], shot['box'], colors['dom']
    record = {'surface': name, 'paintedBox': box, 'changedPixels': shot['changed'], 'colors': colors}
    if not box:
        return record, [f'{name} paints no pixels']
    if box['width'] > dom['width'] + SLACK or box['height'] > dom['height'] + SLACK:
        raise RuntimeError(f'{name}: painted box {box} larger than its element {dom}; wrong surface measured')
    failures = []
    background = None
    if shot['text']:
        background = dominant(image, inflate(box, 4), colors.get('skip'))
        record['background'] = background
        if not near(background, colors['--tf-bg']):
            failures.append(f'{name} paints background {background}, not --tf-bg {colors["--tf-bg"]}')
    if border_var or border_color:
        color = border_color or colors[border_var]
        label = border_var or '--newtab-border-color'
        sides = border_sides(image, box, color)
        record['border'] = {'color': color, 'sides': sides}
        missing = [side for side, ok in sides.items() if not ok]
        if background and near(color, background, DISTINCT):
            failures.append(f'{name} {label} {color} is indistinguishable from its background {background}')
        elif missing:
            failures.append(f'{name} has no {label} border {color} on {", ".join(missing)}')
    if shot['text']:
        exclude = [colors['--tf-border'], colors['--tf-accent']]
        text = [image.pixel(x, y) for x, y in image.coordinates(inflate(box, 4))
                if image.pixel(x, y) != shot['text'].pixel(x, y)]
        strong = sum(1 for p in text if ratio(p, background) >= MIN_RATIO and not any(near(p, c, 16) for c in exclude))
        record['textPixels'] = {'changed': len(text), 'atRatio': strong}
        if strong < MIN_TEXT_PIXELS:
            failures.append(f'{name} text paints {strong} pixels at {MIN_RATIO}:1, fewer than {MIN_TEXT_PIXELS}')
    return record, failures


def wait(m, script, what, timeout=30, sandbox='system'):
    deadline = time.monotonic() + timeout
    while not m.execute_script(script, sandbox=sandbox):
        if time.monotonic() >= deadline:
            raise RuntimeError(f'{what} did not happen')
        time.sleep(.2)


def run(env, nova, config, kind):
    """Launch Firefox with config and return (metadata, records, failures by surface)."""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-surface-') as name:
        directory = Path(name)
        profile = directory / 'profile'
        profile.mkdir()
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(config)
        prefs = {
            'toolkit.legacyUserProfileCustomizations.stylesheets': True,
            'browser.nova.enabled': nova,
            'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
            'browser.shell.checkDefaultBrowser': False,
            'browser.startup.page': 0,
            # Static new tab: live widgets (clock, weather, stories) repaint between captures.
            'browser.newtabpage.activity-stream.widgets.enabled': False,
            'browser.newtabpage.activity-stream.showWeather': False,
            'browser.newtabpage.activity-stream.feeds.section.topstories': False,
            'browser.newtabpage.activity-stream.showSponsoredTopSites': False,
            'ui.prefersReducedMotion': 1,  # no popup open animations
            'marionette.port': port,
        }
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        shots = directory
        if os.environ.get('TF_SURFACE_ARTIFACTS'):
            shots = Path(os.environ['TF_SURFACE_ARTIFACTS']) / f'nova-{str(nova).lower()}-{kind}'
            shots.mkdir(parents=True, exist_ok=True)
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
                "buildID:Services.appinfo.appBuildID, wayland:Services.env.get('WAYLAND_DISPLAY'), "
                "headless:Cc['@mozilla.org/gfx/info;1'].getService(Ci.nsIGfxInfo).isHeadless, dpr:devicePixelRatio, "
                "browserNova:Services.prefs.getBoolPref('browser.nova.enabled'), "
                "newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled', false)}",
                sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['headless'] or not meta['wayland'] or meta['browserNova'] != nova or meta['dpr'] != 1):
                raise RuntimeError(f'unexpected Firefox process, profile, display or preference: {meta}')
            # Marionette stripes the URL bar while under remote control; that is not textfox's paint.
            m.execute_script("document.documentElement.removeAttribute('remotecontrol')", sandbox='system')
            capture = Capture(m, shots, env)
            records, failures = [], {}

            # Two tabs (selected and not), URL bar text, findbar with text.
            m.execute_script("gBrowser.addTrustedTab('about:blank'); gURLBar.value = 'textfox paint probe'; "
                             "gBrowser.getFindBar().then(f => { f.open(); f._findField.value = 'textfox'; })",
                             sandbox='system')
            wait(m, "return gBrowser.tabs.length == 2 && !!document.querySelector('findbar:not([hidden])')",
                 'second tab and findbar')
            time.sleep(1)
            for name, selector, border_var, text_selector, opener in CHROME_SURFACES:
                if opener:
                    m.execute_script(opener[0], sandbox='system')
                    wait(m, opener[1], f'{name} open')
                    time.sleep(1)
                colors = m.execute_script(RESOLVE_JS, script_args=[selector], sandbox='system')
                if not colors:
                    raise RuntimeError(f'{name}: {selector} missing')
                shot = capture.surface(name, selector, text_selector)
                record, failures[name] = judge(name, shot, colors, border_var=border_var)
                records.append(record)

            m.execute_script("document.getElementById('toolbar-context-menu').hidePopup(); "
                             "gBrowser.getFindBar().then(f => f.close(true)); "
                             "gBrowser.selectedTab = gBrowser.addTrustedTab('about:newtab')", sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            wait(m, "return location.href === 'about:newtab' && !!document.querySelector('.top-site-outer .tile')",
                 'new-tab shortcuts', sandbox='default')
            time.sleep(2)
            for name, selectors in NEWTAB_SURFACES.items():
                target = m.execute_script(NEWTAB_MARK_JS, script_args=[selectors])
                if 'missing' in target:
                    raise RuntimeError(f'{name}: none of {selectors} visible; found {target["missing"]}')
                shot = capture.surface(name, '[data-tf-paint-probe]', content=True)
                record, failures[name] = judge(name, shot, target, border_color=target['border'])
                records.append(record)
            return meta, records, failures
        finally:
            try:
                if m:
                    m.delete_session()
            finally:
                stop(process, 15)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config', type=Path, help='config.css to check in place of the sentinel config')
    args = parser.parse_args()
    configs = dict(CONFIGS)
    if args.config:
        del configs['sentinel']
        configs['given'] = args.config.read_text()
    failures, surfaces = [], None
    with tempfile.TemporaryDirectory(prefix='tf-surface-sway-') as name:
        directory = Path(name)
        directory.chmod(0o700)
        env = os.environ.copy()
        sway = start_sway(directory, env)
        try:
            for nova in (False, True):
                for kind, config in configs.items():
                    meta, records, found = run(env, nova, config, kind)
                    print(json.dumps({'nova': nova, 'config': kind, 'firefox': meta, 'surfaces': records}),
                          flush=True)
                    surfaces = surfaces or list(found)
                    if list(found) != surfaces:
                        raise RuntimeError(f'surfaces measured differ: {list(found)}')
                    if kind == 'known-bad':
                        undetected = [s for s, problems in found.items() if not problems]
                        if undetected:
                            raise RuntimeError(f'nova={nova}: known-bad config.css not detected on '
                                               + ', '.join(undetected))
                        continue
                    failures += [f'nova={nova} config={kind}: {p}' for problems in found.values() for p in problems]
        finally:
            stop(sway, 10)
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', file=sys.stderr)
        return 1
    good = ', '.join(k for k in configs if k != 'known-bad')
    print(f'PASS: {len(surfaces)} surfaces paint textfox styling in both browser Nova states ({good}); '
          'the known-bad config.css fails every one')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:  # an untrustworthy run is a failure, never inconclusive
        print(f'FAIL: {error}', file=sys.stderr)
        sys.exit(1)
