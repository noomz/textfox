#!/usr/bin/env python3
"""Check textfox's key surfaces from headed, painted pixels; exit 0 pass, 1 fail.

On Linux, starts a disposable 1280x800 headless Sway compositor, runs Firefox
headed in it, and captures the compositor output with grim (see
test-nova-layout-live.py). On macOS and Windows, runs Firefox headed on the
logged-in desktop, maximized, and captures what the OS composited over its
window (paint_display.py); set FIREFOX to the Firefox binary there.
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

Sizes in device pixels (size slack, border search depth, least text pixels)
scale with devicePixelRatio, so a Retina capture is held to the same bar.

`--high-contrast` (Windows only) turns real OS high contrast on for the run,
as test-high-contrast-windows.py does, and requires the effective
browser.display.document_color_use to be Windows' default 0 with no stand-in
preference set. Firefox's system colors then replace textfox's background and
chrome borders in places, as high contrast intends, so chrome surfaces must
paint readable text (4.5:1 on what painted) and new-tab surfaces their border;
the known-bad config.css must still fail at least one surface, and the run
names the surfaces high contrast kept readable.

`--newtab-defaults` runs the new tab as Firefox ships it, with widgets, stories
and weather on and nothing hidden: the new-tab preferences Marionette overrides
on the default branch are set back to Firefox's own values, the region is GB
(where weather, clocks, crossword, focus timer, lists and stories all ship),
the run asserts those features are effective and rendered, and the widgets must
paint in the captures. Live widgets (the clocks tick each minute) can change a
capture set; an unstable set is retried, never accepted.

`--theme ID[:light|dark]` (repeatable) runs every case with that Firefox theme
active (ff_themes.py: built-in Light, Dark, Alpenglow, or a Nova theme from its
pinned AMO file, which the run downloads once); `--themes default` runs a dark and
a light classic and Nova theme, `--themes every` all theme-picker themes in both
schemes (V15). Surfaces must paint --tf-bg under the theme, as without one.

`--art images|gradient` (V19) turns --tf-theme-images or --tf-theme-gradient on in
every config.css (gradient: with a visible stand-in for Linux's transparent browser
Nova gradient, ff_themes.ART_CONFIG) and holds animated images still. Where Firefox
itself has that art (its own `--toolbox-background-image` at :root: a theme's layers
for images, browser Nova's gradient without a theme for gradient), each window box
(navbar, URL bar text, tabs, findbar) is judged over the art instead of on --tf-bg:
where the art paints with the box hidden, the box must show it (ART_SHOWN of those
pixels unchanged; and under at least one of them ART_MIN_PIXELS art pixels, since a
gradient can equal the frame color under a whole box), and its text must reach 4.5:1
against the paint under each text pixel (the capture with the text hidden). Popups
keep the --tf-bg judge. `--themes art` is the image themes and nova-ash.

`--expect-version` fails the run unless Firefox reports that version. FIREFOX may
be a command line (paint_display.py), for example a nix-portable launch of a
store Firefox; TF_PAINT_X11=1 runs Firefox as an X11 client on Linux.

Linux requires sway, grim, ImageMagick, and the live-tool venv Python; macOS
and Windows need Python with marionette_driver, and macOS needs Screen
Recording permission for the capturing process. New-tab feeds are on, so the
disposable profile may make network requests. Set TF_SURFACE_ARTIFACTS to a
directory to keep captures and masks.
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
import ff_themes
from paint_display import diff, open_display

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
# Stability controls ignore channel differences up to this (of 255): macOS antialiases a native
# popup's rounded corners one unit differently between frames. Below TOLERANCE, so such noise
# cannot make a surface pass or fail (test-a11y-paint-live.py's StableCapture does the same).
CONTROL_NOISE = 2
ART_MIN_PIXELS = 100  # pixels (scaled by dpr squared) where the art must paint under a box
ART_SHOWN = 0.6       # share of those a box must leave showing: text and controls cover the rest
# Windows: a file Firefox's last child still holds must not fail the run (Python 3.10+ only; macOS ships 3.9).
KEEP_LOCKED = {'ignore_cleanup_errors': True} if sys.platform == 'win32' else {}

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

# --newtab-defaults. Marionette's RecommendedPreferences.sys.mjs sets these false on the
# default branch; Firefox 159's own defaults (ActivityStream.sys.mjs PREFS_CONFIG; the widgets
# market gate restricts no region) are true. The region decides which widgets ship.
NEWTAB_DEFAULT_PREFS = {
    'browser.newtabpage.activity-stream.widgets.enabled': True,
    'browser.newtabpage.activity-stream.showSponsoredTopSites': True,
    'browser.search.region': 'GB',
}
# Must be effective true from Firefox's defaults alone (no user value).
NEWTAB_DEFAULT_ON = ['browser.newtabpage.activity-stream.' + k for k in (
    'showWeather', 'system.showWeather', 'feeds.weatherfeed', 'feeds.section.topstories',
    'feeds.system.topstories', 'widgets.system.enabled', 'widgets.weather.enabled')]
NEWTAB_RENDERED = ['.weather-widget', '.clocks-widget', '.ds-layout-widgets article', '.ds-card']
NEWTAB_WIDGETS = '.weather-widget, .ds-layout-widgets'
MIN_WIDGET_PIXELS = 5000  # the default GB widgets paint tens of thousands
UNSTABLE_RETRIES = 4      # a clock tick lands in at most one capture set per minute
NEWTAB_DEFAULTS_JS = r"""
const p = Services.prefs, on = arguments[0];
return {region: ChromeUtils.importESModule('resource://gre/modules/Region.sys.mjs').Region.home,
        off: on.filter(k => !p.getBoolPref(k, false)), userSet: on.filter(k => p.prefHasUserValue(k))};
"""
NEWTAB_RENDERED_JS = r"""
const shown = e => e.getClientRects().length && getComputedStyle(e).visibility === 'visible';
const missing = arguments[0].filter(s => ![...document.querySelectorAll(s)].some(shown));
const pending = [...document.images].filter(i => {
  const r = i.getBoundingClientRect();
  return shown(i) && r.bottom > 0 && r.top < innerHeight && !i.complete;
}).length;
return {missing, pending, stories: [...document.querySelectorAll('.ds-card')].filter(shown).length,
        widgets: [...document.querySelectorAll('.ds-layout-widgets article, .weather-widget')].filter(shown)
          .map(e => String(e.className).split(' ')[0])};
"""

OPEN_URLBAR = ("gURLBar.focus(); gURLBar.search('about:')",
               "return gURLBar.view.isOpen && !!document.querySelector('.urlbarView-row .urlbarView-title')")
OPEN_APP_MENU = ("gURLBar.view.close(); gURLBar.blur(); PanelUI.show()",
                 "return document.getElementById('appMenu-popup').state == 'open'")
# Opened at a screen point as a context menu, as a right-click opens it. Anchored, macOS draws
# it natively (widget.macos.native-anchored-menus) and no userChrome.css applies.
OPEN_CONTEXT = ("PanelUI.hide(); const nav = document.getElementById('nav-bar'); "
                "document.getElementById('toolbar-context-menu').openPopupAtScreen("
                "nav.screenX + 40, nav.screenY + nav.getBoundingClientRect().height, true)",
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
    def __init__(self, path, size=OUTPUT):
        self.width, self.height, self.data = load_ppm(path)
        if (self.width, self.height) != tuple(size):
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


def inflate(box, by, size=OUTPUT):
    x, y = max(0, box['x'] - by), max(0, box['y'] - by)
    right = min(size[0], box['x'] + box['width'] + by)
    bottom = min(size[1], box['y'] + box['height'] + by)
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


def border_sides(image, box, color, search=EDGE_SEARCH):
    """Sides (left, right, bottom) whose outermost `search` lines hold a border of color."""
    x0, y0, w, h = box['x'], box['y'], box['width'], box['height']
    rows = range(y0 + h // 4, y0 + h - h // 4)
    columns = range(x0 + w // 4, x0 + w - w // 4)
    lines = {
        'left': [[(x0 + d, y) for y in rows] for d in range(min(search, w))],
        'right': [[(x0 + w - 1 - d, y) for y in rows] for d in range(min(search, w))],
        'bottom': [[(x, y0 + h - 1 - d) for x in columns] for d in range(min(search, h))],
    }
    return {side: any(line and sum(near(image.pixel(x, y), color) for x, y in line) >= EDGE_MATCH * len(line)
                      for line in candidates)
            for side, candidates in lines.items()}


class Capture:
    def __init__(self, m, directory, env, display=None, retries=0):
        self.m, self.directory, self.env, self.shots = m, directory, env, 0
        self.display, self.retries = display, retries
        self.size = display.size if display else OUTPUT
        # Over theme art (V19) a large image rasterizes 1 unit differently where a region
        # repaints; such noise must not join a surface's painted pixels.
        self.noise = 0

    def shoot(self, css, content):
        self.m.execute_async_script(CONTENT_STYLE_JS if content else CHROME_STYLE_JS,
                                    script_args=[FREEZE + css], sandbox='default' if content else 'system')
        time.sleep(.5)  # let the compositor commit the frame grim reads
        self.shots += 1
        path = self.directory / f'shot-{self.shots}.ppm'
        if self.display:
            return self.display.capture(path)
        subprocess.run(['grim', '-t', 'ppm', str(path)], env=self.env, check=True, stdout=subprocess.DEVNULL)
        return path

    def surface(self, name, selector, text_selector=None, content=False):
        """Base capture, painted box of selector, and text target pixels, with stability controls.

        An unstable capture set is retried up to self.retries times, then fails the run."""
        for attempt in range(self.retries + 1):
            try:
                return self._surface(name, selector, text_selector, content)
            except UnstableCapture as error:
                if attempt == self.retries:
                    raise RuntimeError(str(error))
                print(f'RETRY {error}', file=sys.stderr, flush=True)
                time.sleep(1)

    def _surface(self, name, selector, text_selector, content):
        hide = '{ visibility: hidden !important; }'
        base = self.shoot('', content)
        control = self.shoot('', content)
        hidden = self.shoot(f'{selector} {hide}', content)
        text = self.shoot(f'{text_selector} {hide}', content) if text_selector else None
        restored = self.shoot('', content)
        stem = self.directory / f'mask-{self.shots}'
        controls = {'control': diff(base, control, f'{stem}-control.pgm', CONTROL_NOISE)[0],
                    'restored': diff(base, restored, f'{stem}-restored.pgm', CONTROL_NOISE)[0]}
        if any(controls.values()):
            raise UnstableCapture(f'{name}: captures unstable {controls}')
        changed, box = diff(base, hidden, f'{stem}-{name}.pgm', self.noise)
        return {'image': Image(base, self.size), 'box': box, 'changed': changed,
                'text': Image(text, self.size) if text else None, 'hidden': Image(hidden, self.size)}


class UnstableCapture(RuntimeError):
    pass


def art_shown(shot, box, flat, skip=None):
    """(art pixels, shown): pixels in box where the capture with the surface hidden is not flat
    --tf-bg, and how many of them the surface leaves unchanged. The surface's text target is
    hidden too (its own capture), so text does not count as covering the art."""
    image, hidden = shot['text'] or shot['image'], shot['hidden']
    art = shown = 0
    for x, y in image.coordinates(box):
        if skip and inside(x, y, skip):
            continue
        under = hidden.pixel(x, y)
        if near(under, flat):
            continue
        art += 1
        shown += near(image.pixel(x, y), under)
    return art, shown


def judge(name, shot, colors, border_var=None, border_color=None, dpr=1, readable_only=False, art=False):
    """Return (record, failures) for one surface; raise when the measurement is not of that surface.

    Device-pixel sizes scale with dpr: the element box and slack by dpr, the text floor by its square.
    readable_only (OS high contrast): Firefox's system colors may replace textfox's background and
    chrome borders, as high contrast intends; only the text must reach MIN_RATIO on what painted.
    art (V19): the surface sits over theme art; it must show the art, and text is judged against
    the paint under each text pixel. Whether any art paints under a box at all is the run's
    check (art_under_some), since a gradient can equal the frame color under a whole box."""
    image, box, dom = shot['image'], shot['box'], colors['dom']
    record = {'surface': name, 'paintedBox': box, 'changedPixels': shot['changed'], 'colors': colors}
    if not box:
        return record, [f'{name} paints no pixels']
    if box['width'] > (dom['width'] + SLACK) * dpr or box['height'] > (dom['height'] + SLACK) * dpr:
        raise RuntimeError(f'{name}: painted box {box} larger than its element {dom}; wrong surface measured')
    failures = []
    background = None
    size = (image.width, image.height)
    if art:
        background = tuple(colors['--tf-bg'])
        pixels, shown = art_shown(shot, inflate(box, -4, size), background, colors.get('skip'))
        record['art'] = {'pixels': pixels, 'shown': shown, 'least': round(ART_MIN_PIXELS * dpr * dpr)}
        if shown < ART_SHOWN * pixels:
            failures.append(f'{name} covers the theme art: shows {shown} of {pixels} art pixels')
    elif shot['text']:
        background = dominant(image, inflate(box, 4, size), colors.get('skip'))
        record['background'] = background
        if not readable_only and not near(background, colors['--tf-bg']):
            failures.append(f'{name} paints background {background}, not --tf-bg {colors["--tf-bg"]}')
    if border_color or (border_var and not readable_only):
        color = border_color or colors[border_var]
        label = border_var or '--newtab-border-color'
        sides = border_sides(image, box, color, round(EDGE_SEARCH * dpr))
        record['border'] = {'color': color, 'sides': sides}
        missing = [side for side, ok in sides.items() if not ok]
        if background and near(color, background, DISTINCT):
            failures.append(f'{name} {label} {color} is indistinguishable from its background {background}')
        elif missing:
            failures.append(f'{name} has no {label} border {color} on {", ".join(missing)}')
    if shot['text']:
        # Leave out the surface's own border, which inflate() takes in. Excluding --tf-accent
        # everywhere dropped Firefox 160's de-emphasized macOS tab labels (black at 0.6 on white,
        # 5.7:1), which paint within 16 of the default accent (B17). Borderless: both.
        decoration = border_color or (colors[border_var] if border_var else None)
        exclude = [decoration] if decoration else [colors['--tf-border'], colors['--tf-accent']]
        text = [image.pixel(x, y) for x, y in image.coordinates(inflate(box, 4, size))
                if image.pixel(x, y) != shot['text'].pixel(x, y)]
        if art:  # each text pixel against what paints under it
            pairs = [(image.pixel(x, y), shot['text'].pixel(x, y)) for x, y in image.coordinates(inflate(box, 4, size))
                     if image.pixel(x, y) != shot['text'].pixel(x, y)]
            strong = sum(1 for p, under in pairs
                         if ratio(p, under) >= MIN_RATIO and not any(near(p, c, 16) for c in exclude))
        else:
            strong = sum(1 for p in text
                         if ratio(p, background) >= MIN_RATIO and not any(near(p, c, 16) for c in exclude))
        record['textPixels'] = {'changed': len(text), 'atRatio': strong}
        least = round(MIN_TEXT_PIXELS * dpr * dpr)
        if strong < least:
            failures.append(f'{name} text paints {strong} pixels at {MIN_RATIO}:1, fewer than {least}')
    return record, failures


def art_under_some(records):
    """None, or the failure when no surface judged over art had at least its least art pixels under it."""
    over = [r for r in records if 'art' in r]
    if over and not any(r['art']['pixels'] >= r['art']['least'] for r in over):
        return (f'theme art paints under none of {", ".join(r["surface"] for r in over)} '
                f'(art pixels {[r["art"]["pixels"] for r in over]})')
    return None


def wait(m, script, what, timeout=30, sandbox='system'):
    deadline = time.monotonic() + timeout
    while not m.execute_script(script, sandbox=sandbox):
        if time.monotonic() >= deadline:
            raise RuntimeError(f'{what} did not happen')
        time.sleep(.2)


def neutral_pointer(m):
    """Park the pointer in the window's top-left margin so no bordered box shows its hover color.

    The real cursor stays wherever the desktop left it. On macOS it rested on the findbar's
    title once T27 centered titles on their borders, and the hover border failed the judge."""
    m.actions.sequence('pointer', 'mouse', {'pointerType': 'mouse'}).pointer_move(2, 2, origin='viewport').perform()


def run(display, nova, config, kind, high_contrast=None, newtab_defaults=False, expect_version=None, theme=None,
        art='none'):
    """Launch Firefox with config on display and return (metadata, records, failures by surface).

    high_contrast: the OS high-contrast state the run set (Windows), or None where not set.
    newtab_defaults: the new tab as Firefox ships it (widgets, stories, weather), nothing hidden.
    theme: a ff_themes spec enabled before any capture, or None for no theme.
    art: an ff_themes.ART_CONFIG mode; config already carries its config.css lines."""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-surface-', **KEEP_LOCKED) as name:
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
            # Windows: ASRouter messages (app menu, startup, remote) ask the OS to pin Firefox
            # to the taskbar, and the OS prompt covers the surfaces. Every provider off.
            'browser.newtabpage.activity-stream.asrouter.userprefs.cfr.features': False,
            **{f'browser.newtabpage.activity-stream.asrouter.providers.{p}': json.dumps(
                {'id': p, 'enabled': False, 'type': t, **extra}) for p, t, extra in (
                ('onboarding', 'local', {'localProvider': 'OnboardingMessageProvider', 'exclude': []}),
                ('cfr', 'remote-settings', {'collection': 'cfr', 'updateCycleInMs': 3600000}),
                ('message-groups', 'remote-settings', {'collection': 'message-groups', 'updateCycleInMs': 3600000}),
                ('messaging-experiments', 'remote-experiments', {'updateCycleInMs': 3600000}))},
            # A menu taller than a small screen scrolls; overlay scrollbars fade between captures.
            'ui.useOverlayScrollbars': 0,
            'marionette.port': port,
        }
        if newtab_defaults:
            for k in ('widgets.enabled', 'showWeather', 'feeds.section.topstories', 'showSponsoredTopSites'):
                del prefs[f'browser.newtabpage.activity-stream.{k}']
            prefs.update(NEWTAB_DEFAULT_PREFS)
        if theme:
            prefs.update(ff_themes.prefs(theme))
        # textfox's shipped prefs first, as installed (macOS: no native context menus);
        # the probe's own prefs follow and win where both set one.
        (profile / 'user.js').write_text((ROOT / 'user.js').read_text() + ''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        shots = directory
        if os.environ.get('TF_SURFACE_ARTIFACTS'):
            shots = Path(os.environ['TF_SURFACE_ARTIFACTS']) / f'nova-{str(nova).lower()}-{kind}'
            shots.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen([*display.firefox, '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env={**display.firefox_env, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'},
                                   **display.popen_options(),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script(
                "return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, "
                "buildID:Services.appinfo.appBuildID, wayland:Services.env.get('WAYLAND_DISPLAY'), "
                "headless:Cc['@mozilla.org/gfx/info;1'].getService(Ci.nsIGfxInfo).isHeadless, dpr:devicePixelRatio, "
                "browserNova:Services.prefs.getBoolPref('browser.nova.enabled'), os:Services.appinfo.OS, "
                "newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled', false), "
                "colorUse:Services.prefs.getIntPref('browser.display.document_color_use'), "
                "colorUseSet:Services.prefs.prefHasUserValue('browser.display.document_color_use'), "
                "a11yThemeSet:Services.prefs.prefHasUserValue('ui.useAccessibilityTheme'), "
                "contrastMore:matchMedia('(prefers-contrast: more)').matches, "
                "windowProtocol:(() => { try { return Cc['@mozilla.org/gfx/info;1'].getService(Ci.nsIGfxInfo)"
                ".windowProtocol || null; } catch (e) { return null; } })(), "
                "layerManager:window.windowUtils.layerManagerType}",
                sandbox='system')
            problems = display.check(meta)
            if high_contrast is not None and meta['contrastMore'] != high_contrast:
                problems.append(f'prefers-contrast: more is {meta["contrastMore"]}, OS high contrast {high_contrast}')
            if high_contrast and (meta['colorUse'] != 0 or meta['colorUseSet'] or meta['a11yThemeSet']):
                problems.append('high contrast not in effect from the OS alone')
            if process.poll() is not None:
                problems.append(f'launched process exited ({process.returncode})')
            if Path(meta['profile']).resolve() != profile.resolve():
                problems.append(f'profile {meta["profile"]}, expected {profile}')
            if meta['headless'] or meta['browserNova'] != nova:
                problems.append('headless, or browser Nova not as set')
            if expect_version and meta['version'] != expect_version:
                problems.append(f'Firefox {meta["version"]}, expected {expect_version}')
            if newtab_defaults:
                meta['newtabDefaults'] = m.execute_script(NEWTAB_DEFAULTS_JS, script_args=[NEWTAB_DEFAULT_ON],
                                                          sandbox='system')
                found = meta['newtabDefaults']
                if found['region'] != NEWTAB_DEFAULT_PREFS['browser.search.region'] or found['off'] or found['userSet']:
                    problems.append(f'new tab not at Firefox defaults: {found}')
            if problems:
                raise RuntimeError(f'unexpected Firefox process, profile, display or preference {problems}: {meta}')
            if theme:
                meta['theme'] = ff_themes.apply(m, theme)
            meta['art'] = ff_themes.art_expected(m, art)
            ff_themes.hold_still(m, art)
            meta['window'] = display.bind(m)
            meta['platform'] = display.platform
            # Marionette stripes the URL bar while under remote control; that is not textfox's paint.
            m.execute_script("document.documentElement.removeAttribute('remotecontrol')", sandbox='system')
            capture = Capture(m, shots, display.env, display, retries=UNSTABLE_RETRIES if newtab_defaults else 0)
            records, failures = [], {}

            # Two tabs (selected and not), URL bar text, findbar with text.
            m.execute_script("gBrowser.addTrustedTab('about:blank'); gURLBar.value = 'textfox paint probe'; "
                             "gBrowser.getFindBar().then(f => { f.open(); f._findField.value = 'textfox'; })",
                             sandbox='system')
            wait(m, "return gBrowser.tabs.length == 2 && !!document.querySelector('findbar:not([hidden])')",
                 'second tab and findbar')
            neutral_pointer(m)
            time.sleep(1)
            for name, selector, border_var, text_selector, opener in CHROME_SURFACES:
                if opener:
                    m.execute_script(opener[0], sandbox='system')
                    wait(m, opener[1], f'{name} open')
                    neutral_pointer(m)  # a popup opens under the pointer; keep its items unhovered
                    time.sleep(1)
                colors = m.execute_script(RESOLVE_JS, script_args=[selector], sandbox='system')
                if not colors:
                    raise RuntimeError(f'{name}: {selector} missing')
                # Only over art: a popup's --tf-bg can sit within noise of faint art behind it.
                capture.noise = CONTROL_NOISE if meta['art']['expected'] and not opener else 0
                shot = capture.surface(name, selector, text_selector)
                capture.noise = 0
                record, failures[name] = judge(name, shot, colors, border_var=border_var, dpr=display.dpr,
                                               readable_only=bool(high_contrast),
                                               art=meta['art']['expected'] and not opener)
                records.append(record)

            missing_art = art_under_some(records)
            if missing_art:
                failures['navbar'].append(missing_art)
            m.execute_script("document.getElementById('toolbar-context-menu').hidePopup(); "
                             "gBrowser.getFindBar().then(f => f.close(true)); "
                             "gBrowser.selectedTab = gBrowser.addTrustedTab('about:newtab')", sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            wait(m, "return location.href === 'about:newtab' && !!document.querySelector('.top-site-outer .tile')",
                 'new-tab shortcuts', sandbox='default')
            time.sleep(2)
            if newtab_defaults:
                # Firefox's default widgets, stories and weather, rendered and painted, not hidden.
                deadline = time.monotonic() + 60
                while True:
                    rendered = m.execute_script(NEWTAB_RENDERED_JS, script_args=[NEWTAB_RENDERED])
                    if not rendered['missing'] and not rendered['pending']:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'default new tab did not render: {rendered}')
                    time.sleep(1)
                time.sleep(2)
                m.execute_script("for (const e of document.querySelectorAll('[data-tf-paint-probe]')) "
                                 "e.removeAttribute('data-tf-paint-probe'); for (const e of document."
                                 f"querySelectorAll({json.dumps(NEWTAB_WIDGETS)})) e.setAttribute('data-tf-paint-probe', '')")
                widgets = capture.surface('newtab-widgets', '[data-tf-paint-probe]', content=True)
                rendered['widgetPixels'] = widgets['changed']
                meta['newtabDefaults']['rendered'] = rendered
                if widgets['changed'] < MIN_WIDGET_PIXELS:
                    raise RuntimeError(f'default new-tab widgets paint {widgets["changed"]} pixels, '
                                       f'fewer than {MIN_WIDGET_PIXELS}: {rendered}')
            for name, selectors in NEWTAB_SURFACES.items():
                target = m.execute_script(NEWTAB_MARK_JS, script_args=[selectors])
                if 'missing' in target:
                    raise RuntimeError(f'{name}: none of {selectors} visible; found {target["missing"]}')
                shot = capture.surface(name, '[data-tf-paint-probe]', content=True)
                record, failures[name] = judge(name, shot, target, border_color=target['border'], dpr=display.dpr)
                records.append(record)
            return meta, records, failures
        finally:
            try:
                if m:
                    m.delete_session()
            finally:
                display.stop_firefox(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config', type=Path, help='config.css to check in place of the sentinel config')
    parser.add_argument('--newtab-defaults', action='store_true',
                        help="new tab as Firefox ships it: widgets, stories and weather on, nothing hidden")
    parser.add_argument('--expect-version', help='fail unless Firefox reports this version, e.g. 159.0.1')
    parser.add_argument('--theme', action='append', default=[], help='Firefox theme ID[:light|dark] (repeatable)')
    parser.add_argument('--themes', choices=('default', 'every', 'art'), help='theme set: default (dark and light, '
                        'classic and Nova), every theme-picker theme in both schemes, or art (image themes, nova-ash)')
    parser.add_argument('--art', choices=tuple(ff_themes.ART_CONFIG), default='none',
                        help='turn --tf-theme-images or --tf-theme-gradient on (V19)')
    parser.add_argument('--high-contrast', action='store_true',
                        help='Windows only: run with real OS high contrast on (High Contrast Black)')
    args = parser.parse_args()
    hc = None
    if args.high_contrast and sys.platform != 'win32':
        raise RuntimeError('--high-contrast needs Windows')
    if sys.platform == 'win32':  # set OS high contrast either way: a state left on would skew a plain run
        hc = runpy.run_path(str(HERE / 'test-high-contrast-windows.py'))
    themes = args.theme + {'default': ff_themes.DEFAULT_THEMES, 'every': ff_themes.EVERY_THEME,
                           'art': ff_themes.ART_THEMES, None: []}[args.themes]
    for spec in themes:
        ff_themes.parse(spec)
    configs = dict(CONFIGS)
    if args.config:
        del configs['sentinel']
        configs['given'] = args.config.read_text()
    configs = {k: v + ff_themes.ART_CONFIG[args.art] for k, v in configs.items()}
    failures, surfaces, hc_kept, judged_art = [], None, {}, False
    with tempfile.TemporaryDirectory(prefix='tf-surface-display-', **KEEP_LOCKED) as name:
        directory = Path(name)
        directory.chmod(0o700)
        display = open_display(directory, os.environ.copy())
        initial = hc['high_contrast']()[0] if hc else None
        try:
            if hc:
                hc['set_high_contrast'](args.high_contrast)
            for theme, nova, kind in [(t, n, k) for t in themes or [None] for n in (False, True) for k in configs]:
                config = configs[kind]
                meta, records, found = run(display, nova, config, kind,
                                            high_contrast=args.high_contrast if hc else None,
                                            newtab_defaults=args.newtab_defaults,
                                            expect_version=args.expect_version, theme=theme, art=args.art)
                judged_art = judged_art or meta['art']['expected']
                print(json.dumps({'nova': nova, 'config': kind, 'theme': theme, 'art': args.art, 'firefox': meta,
                                  'surfaces': records}), flush=True)
                nova = f'{nova} theme={theme}' if theme else nova
                surfaces = surfaces or list(found)
                if list(found) != surfaces:
                    raise RuntimeError(f'surfaces measured differ: {list(found)}')
                if kind == 'known-bad':
                    undetected = [s for s, problems in found.items() if not problems]
                    if args.high_contrast:
                        # High contrast keeps some surfaces readable whatever config.css says;
                        # the check must still fail where textfox's colors reach the screen.
                        if len(undetected) == len(found):
                            raise RuntimeError(f'nova={nova}: known-bad config.css not detected anywhere')
                        hc_kept[nova] = undetected
                    elif undetected:
                        raise RuntimeError(f'nova={nova}: known-bad config.css not detected on '
                                           + ', '.join(undetected))
                    continue
                failures += [f'nova={nova} config={kind}: {p}' for problems in found.values() for p in problems]
        finally:
            try:
                if hc:
                    hc['set_high_contrast'](initial)
            finally:
                display.close()
    if args.art != 'none' and not judged_art:  # an --art run that judged nothing over art proves nothing
        failures.append(f'--art {args.art}: Firefox had no such art in any run; nothing was judged over art')
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', file=sys.stderr)
        return 1
    good = ', '.join(k for k in configs if k != 'known-bad')
    where = display.platform + (' with OS high contrast' if args.high_contrast else '') + (
        f' with --art {args.art}' if args.art != 'none' else '') + (
        f' with Firefox themes {", ".join(themes)}' if themes else '') + (
        ' with the default new tab (widgets, stories, weather)' if args.newtab_defaults else '')
    if args.high_contrast:
        print(f'PASS: {len(surfaces)} surfaces paint readable text and new-tab borders on {where} in both browser '
              f'Nova states ({good}); the known-bad config.css fails the rest, high contrast kept readable: '
              + '; '.join(f'nova={n}: {", ".join(k) or "none"}' for n, k in hc_kept.items()))
        return 0
    print(f'PASS: {len(surfaces)} surfaces paint textfox styling on {where} in both browser Nova states ({good}); '
          'the known-bad config.css fails every one')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:  # an untrustworthy run is a failure, never inconclusive
        print(f'FAIL: {error}', file=sys.stderr)
        sys.exit(1)
